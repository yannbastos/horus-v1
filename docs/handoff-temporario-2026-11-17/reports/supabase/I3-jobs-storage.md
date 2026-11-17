# HOR-164 / I3 — Jobs, Mídia, WorkerLock e Fronteira de Repositório (`storage.py`)

## Contrato da fronteira de repositório (`storage.py`, ≤ 40 linhas)

```python
class DatabaseUnavailable(DatabaseRefused): # HTTP 503, code="database_unavailable"
class LeaseLostError(RuntimeError):         # lease expirado ou fencing_token superado

class Store:
    def __init__(self, db: DatabaseManager | None, *, data_dir: Path | None = None)
    @property
    def database_id(self) -> str            # lê horus.database_identity (singleton=true)
    @contextmanager
    def uow(self, *, readonly: bool = False) -> Iterator[psycopg.Connection]
    # Uso em I4/I5/I7:
    #   with store.uow() as conn:           # 1 conexão do pool, conn.transaction() explícita
    #       with conn.cursor(row_factory=dict_row) as cur: ...
    #   with store.uow(readonly=True) as conn: # SET LOCAL TRANSACTION READ ONLY
    # Erros estáveis:
    #   - DatabaseUnavailable (503, code="database_unavailable"): db=None, DatabaseRefused,
    #     psycopg.OperationalError/PoolTimeout ou psycopg.InterfaceError (nunca fallback SQLite).
    #   - LeaseLostError: escrita/renovação com worker_instance_id/fencing_token inválido/expirado.
    #   - ValueError: chave de idempotência reutilizada com payload_canonical divergente.
    #   - KeyError: job_id ausente em event().
```

- Helpers exportados para I4/I5/I7: `canonical_json_bytes(value) -> bytes` (`sort_keys=True, ensure_ascii=False`, UTF-8) e `now() -> str` (UTC ISO-8601).
- Regras de uso: não há `store.connect()` nem `store.checkpoint()` SQLite; nunca abrir transação manual fora de `store.uow()`; sempre usar `dict_row` no cursor quando precisar de dicionários sem mutar `conn.row_factory` da conexão do pool.

## O que foi migrado em I3

- `backend/src/horus_backend/storage.py`: `Store` 100% PostgreSQL via `DatabaseManager.connection()`, `horus.jobs` (`payload` jsonb + `payload_canonical` bytea, `ON CONFLICT (idempotency_key) DO NOTHING`), `horus.events` (`RETURNING sequence`, poda em 2000 eventos, checkpoint atômico de `transcription.run`), `horus.media` (`ON CONFLICT (path) DO UPDATE`), `claim_job`/`renew_job_lease`/`recover` com `FOR UPDATE SKIP LOCKED` e fencing.
- `backend/src/horus_backend/worker_lock.py`: `WorkerLock` sobre `horus.worker_instances` com `pg_advisory_xact_lock(164, 100)`, exclusividade por `device_id` enquanto `lease_until > clock_timestamp()`, `fencing` monotônico (`GREATEST(MAX(fencing), MAX(fencing_token)) + 1`), `renew()` e expiração segura em `close()`.
- `backend/src/horus_backend/jobs.py`: `JobManager` usa `WorkerLock` PG, `_lease_loop` periódico, `claim_job` antes de iniciar comando/processo, `emit`/`update` com `worker_instance_id` e `fencing_token`, tratamento de `LeaseLostError` e remoção do caminho `.sqlite3` do Radar.

## Dependências de SQLite ainda existentes fora de I3 (para o gate final)

Durante o desenvolvimento híbrido, os módulos abaixo (fora do escopo de I3) ainda importam `Store`/`now` de `storage.py` ou abrem SQLite diretamente:

- `api.py`: instancia `Store(settings.data_dir / "horus.sqlite3")`, chama `store.connect()`, executa `PRAGMA integrity_check`/`user_version` e instancia `RadarState(...sqlite3)` (escopo I-final).
- `workflows.py`, `production.py`, `scripts.py`, `license_reviews.py`, `human_checkpoints.py`, `transmissions.py`, `security.py`, `workflow_hermes.py`: ainda chamam `store.connect()` com SQL SQLite/`BEGIN IMMEDIATE`/`?` (escopos I4 e I5).
- `retention.py`: ainda chama `store.connect()` e `store.checkpoint()` SQLite (escopo I6b).
- `error_center.py`, `notifications.py`, `data_deletion.py`, `publishing.py`, `exports.py`, `narration/service.py`: ainda chamam `store.connect()` (escopos I7 e I8).
- `backups.py`, `local_data_export.py`, `migrations.py`, `persistence_contracts.py`, `radar_youtube/state.py`, `radar_youtube/__main__.py`, `review_comments.py`, `project_import.py`: SQLite/JSON diretos (escopos I6a, I7, I8, I-final).
