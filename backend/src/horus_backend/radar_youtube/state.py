"""Cache/cota/datasets e trava do coletor Radar em PostgreSQL via UoW de I1."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
import time
import uuid

from ..database.manager import DatabaseRefused
from .models import Dataset

# Namespace fixo do aplicativo (mesmo prefixo 'HORU' de I1) para locks consultivos do Radar.
RADAR_LOCK_NS = 1213157973
RADAR_COLLECTION_LOCK_KEY = 16406
RADAR_QUOTA_NS = 16407


def utcnow():
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class BudgetExceeded(RuntimeError):
    pass


class CollectionCancelled(RuntimeError):
    """Cancelamento cooperativo; nenhuma revisão parcial é salva."""


class CollectionLockTimeout(TimeoutError):
    """Outra coleta reteve a trava além do limite local de espera."""


def check_cancelled(cancel):
    if cancel is not None and cancel():
        raise CollectionCancelled('Coleta cancelada pelo chamador.')


class RadarState:
    def __init__(self, db, *, clock=utcnow, lock_timeout=5):
        if not math.isfinite(lock_timeout) or lock_timeout < 0:
            raise ValueError('Timeout da trava deve ser finito e não negativo.')
        if db is None or not hasattr(db, 'connection'):
            raise ValueError('RadarState requer um gerenciador PostgreSQL (DatabaseManager) com .connection().')
        self.db = db
        self.clock = clock
        self.lock_timeout = lock_timeout

    def _now(self) -> datetime:
        return _as_utc(self.clock())

    @contextmanager
    def connect(self):
        """Uma conexão e transação explícita por unidade de trabalho."""
        try:
            with self.db.connection() as conn:
                with conn.transaction():
                    yield conn
        except DatabaseRefused:
            raise

    @contextmanager
    def collection_lock(self, cancel=None):
        """Trava global por advisory lock de sessão no PostgreSQL; liberada em crash ou saída.

        Usa conexão dedicada em autocommit sem transação aberta durante a coleta,
        permitindo que reservas e cache usem suas próprias transações curtas.
        """
        deadline = time.monotonic() + self.lock_timeout
        with self.db.connection() as conn:
            acquired = False
            try:
                while True:
                    check_cancelled(cancel)
                    row = conn.execute(
                        'SELECT pg_try_advisory_lock(%s, %s)',
                        (RADAR_LOCK_NS, RADAR_COLLECTION_LOCK_KEY),
                    ).fetchone()
                    if row and row[0]:
                        acquired = True
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CollectionLockTimeout(
                            'Tempo de espera pela trava do Radar excedido; tente novamente após a coleta em andamento.'
                        ) from None
                    time.sleep(min(0.05, remaining))
                check_cancelled(cancel)
                yield
            finally:
                if acquired:
                    try:
                        conn.execute(
                            'SELECT pg_advisory_unlock(%s, %s)',
                            (RADAR_LOCK_NS, RADAR_COLLECTION_LOCK_KEY),
                        )
                    except Exception:
                        pass

    def purge(self):
        now = self._now()
        window_start = now - timedelta(hours=26)
        with self.connect() as db:
            db.execute('DELETE FROM horus.radar_cache WHERE expires <= %s', (now,))
            db.execute('DELETE FROM horus.radar_datasets WHERE expires <= %s', (now,))
            db.execute('DELETE FROM horus.radar_usage WHERE at <= %s', (window_start,))
            db.execute('DELETE FROM horus.radar_blocks WHERE until <= %s', (now,))

    def cached(self, key):
        self.purge()
        with self.connect() as db:
            row = db.execute(
                'SELECT payload, fetched FROM horus.radar_cache WHERE key = %s',
                (key,),
            ).fetchone()
        if not row:
            return None
        payload, fetched = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        return (payload, _as_utc(fetched))

    def cache(self, key, payload, fetched, hours):
        if not 0 < hours <= 24:
            raise ValueError('Cache deve durar até 24 horas.')
        fetched_utc = _as_utc(fetched)
        expires_utc = fetched_utc + timedelta(hours=hours)
        with self.connect() as db:
            db.execute(
                """INSERT INTO horus.radar_cache (key, fetched, expires, payload)
                   VALUES (%s, %s, %s, %s::jsonb)
                   ON CONFLICT (key) DO UPDATE SET
                       fetched = EXCLUDED.fetched,
                       expires = EXCLUDED.expires,
                       payload = EXCLUDED.payload""",
                (key, fetched_utc, expires_utc, json.dumps(payload, ensure_ascii=False)),
            )

    def reserve(self, bucket, limit):
        """Janela móvel conservadora de 26h em UTC, serializada por bucket via advisory xact lock."""
        now = self._now()
        window_start = now - timedelta(hours=26)
        with self.connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(%s, hashtext(%s))', (RADAR_QUOTA_NS, bucket))
            db.execute('DELETE FROM horus.radar_usage WHERE at <= %s', (window_start,))
            db.execute('DELETE FROM horus.radar_blocks WHERE until <= %s', (now,))
            if db.execute(
                'SELECT 1 FROM horus.radar_blocks WHERE bucket = %s AND until > %s',
                (bucket, now),
            ).fetchone():
                raise BudgetExceeded('Cota bloqueada após recusa do servidor; aguarde a janela local.')
            used = db.execute(
                'SELECT COUNT(*) FROM horus.radar_usage WHERE bucket = %s AND at > %s',
                (bucket, window_start),
            ).fetchone()[0]
            if used >= limit:
                raise BudgetExceeded('Limite local da janela móvel de 26h atingido.')
            db.execute(
                'INSERT INTO horus.radar_usage (id, at, bucket) VALUES (%s, %s, %s)',
                (uuid.uuid4().hex, now, bucket),
            )

    def block(self, bucket):
        until = self._now() + timedelta(hours=26)
        with self.connect() as db:
            db.execute(
                """INSERT INTO horus.radar_blocks (bucket, until)
                   VALUES (%s, %s)
                   ON CONFLICT (bucket) DO UPDATE SET until = EXCLUDED.until""",
                (bucket, until),
            )

    def invalidate(self, kind, identifiers):
        """Remove cópias gerenciadas antigas quando a consulta atual não retorna um recurso."""
        targets = list(identifiers)
        if not targets:
            return
        with self.connect() as db:
            db.execute(
                """DELETE FROM horus.radar_cache
                   WHERE EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(COALESCE(payload->'items', '[]'::jsonb)) AS item
                       WHERE item->>'id' = ANY(%s)
                   )""",
                (targets,),
            )
            db.execute(
                """DELETE FROM horus.radar_datasets
                   WHERE EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(COALESCE(payload->'records', '[]'::jsonb)) AS item
                       WHERE item->>'kind' = %s AND item->>'youtube_id' = ANY(%s)
                   )""",
                (kind, targets),
            )

    def save(self, dataset: Dataset):
        if _as_utc(dataset.expires_at) <= self._now():
            raise ValueError('Não é permitido salvar dataset expirado.')
        with self.connect() as db:
            db.execute(
                """INSERT INTO horus.radar_datasets (id, revision, expires, collected_at, payload)
                   VALUES (%s, %s, %s, %s, %s::jsonb)""",
                (
                    dataset.snapshot_id,
                    dataset.revision,
                    _as_utc(dataset.expires_at),
                    _as_utc(dataset.collected_at),
                    dataset.model_dump_json(),
                ),
            )

    def load(self, snapshot_id, revision):
        self.purge()
        with self.connect() as db:
            row = db.execute(
                'SELECT payload FROM horus.radar_datasets WHERE id = %s AND revision = %s',
                (snapshot_id, revision),
            ).fetchone()
        if not row:
            raise KeyError('Dataset ausente ou expirado.')
        payload = row[0]
        if isinstance(payload, dict):
            return Dataset.model_validate(payload)
        return Dataset.model_validate_json(payload)

    def list_datasets(self):
        self.purge()
        with self.connect() as db:
            rows = db.execute(
                """SELECT d.payload
                   FROM horus.radar_datasets d
                   WHERE d.revision = (
                       SELECT MAX(r.revision) FROM horus.radar_datasets r WHERE r.id = d.id
                   )
                   ORDER BY d.collected_at DESC, d.id DESC
                   LIMIT 100"""
            ).fetchall()
        result = []
        for row in rows:
            payload = row[0]
            if isinstance(payload, dict):
                result.append(Dataset.model_validate(payload))
            else:
                result.append(Dataset.model_validate_json(payload))
        return result
