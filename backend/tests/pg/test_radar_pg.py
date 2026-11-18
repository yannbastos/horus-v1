"""Testes PostgreSQL do Radar YouTube (HOR-164 / I6): cache, cota concorrente, trava, CLI e porte do legado."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event
from time import monotonic
from urllib.error import HTTPError

import psycopg
import pytest
from pydantic import ValidationError

from horus_backend.database.manager import DatabaseManager, DatabaseRefused
from horus_backend.database.vault import ConnectionVault
from horus_backend.radar_youtube import CollectionCancelled, CollectionLockTimeout
from horus_backend.radar_youtube.__main__ import main as radar_cli_main
from horus_backend.radar_youtube.collector import YouTubeCollector, duration, number
from horus_backend.radar_youtube.indicators import opportunity_indicators
from horus_backend.radar_youtube.models import CollectionRequest, Dataset, MetricsPermission, SearchRequest
from horus_backend.radar_youtube.references import channel_reference, video_id
from horus_backend.radar_youtube.state import BudgetExceeded, RadarState
from horus_backend.radar_youtube.transport import CollectionError, GoogleTransport, NoRedirect
from schema_support import schema_database

VIDEO = 'aaaaaaaaaaa'
CHANNEL = 'UC' + 'a' * 22


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Rede real proibida neste teste')
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture(scope='module')
def _radar_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker('pg_strong'):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
        return
    admin, pool = request.getfixturevalue('_radar_schema_db')
    try:
        yield pool
    finally:
        admin.execute(
            'TRUNCATE horus.radar_cache, horus.radar_usage, horus.radar_blocks, horus.radar_datasets RESTART IDENTITY CASCADE'
        )


@pytest.fixture
def state(pg_pool):
    now = [datetime(2026, 11, 11, tzinfo=timezone.utc)]
    result = RadarState(pg_pool, clock=lambda: now[0])
    result.advance = lambda **kwargs: now.__setitem__(0, now[0] + timedelta(**kwargs))
    return result


def video(identifier=VIDEO, views='10', **kwargs):
    return {
        'id': identifier,
        'snippet': {
            'title': 'Referência',
            'channelId': CHANNEL,
            'channelTitle': 'Canal',
            'publishedAt': '2026-10-01T00:00:00Z',
            'liveBroadcastContent': 'none',
        },
        'contentDetails': {'duration': 'PT1M30S'},
        'statistics': {'viewCount': views, **kwargs},
    }


def channel():
    return {
        'id': CHANNEL,
        'snippet': {'title': 'Canal'},
        'statistics': {'subscriberCount': '1200', 'hiddenSubscriberCount': False},
        'contentDetails': {'relatedPlaylists': {'uploads': 'UU' + 'a' * 22}},
    }


class Fake:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, resource, params):
        self.calls.append((resource, deepcopy(params)))
        return deepcopy(self.handler(resource, params))


def collector(state, rows=None, **kwargs):
    fake = Fake(lambda resource, params: {'items': rows if resource == 'videos' else [channel()]})
    return YouTubeCollector(fake, state, sleep=lambda _: None, **kwargs), fake


def test_no_sqlite_import_in_radar_or_retention():
    for module_name in (
        'horus_backend.radar_youtube.state',
        'horus_backend.radar_youtube.__main__',
        'horus_backend.retention',
    ):
        __import__(module_name)
    for mod in list(sys.modules):
        if mod.startswith(('horus_backend.radar_youtube', 'horus_backend.retention')):
            source = Path(sys.modules[mod].__file__).read_text(encoding='utf-8')
            assert 'sqlite3' not in source
            assert '_sqlite3' not in source


def test_radar_state_rejects_sqlite_path_and_unavailable_db(tmp_path):
    with pytest.raises(ValueError, match='DatabaseManager'):
        RadarState(tmp_path / 'radar-youtube.sqlite3')
    assert not (tmp_path / 'radar-youtube.sqlite3').exists()

    class MemoryKeyring:
        def get_password(self, *_):
            return None

    manager = DatabaseManager(ConnectionVault(tmp_path, MemoryKeyring()))
    state = RadarState(manager)
    with pytest.raises(DatabaseRefused) as caught:
        state.purge()
    assert caught.value.http_status == 503
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize('reference', [
    VIDEO,
    'https://youtu.be/' + VIDEO,
    'https://www.youtube.com/watch?v=' + VIDEO,
    'https://youtube.com/shorts/' + VIDEO,
    'https://youtube.com/live/' + VIDEO,
])
def test_video_references(reference):
    assert video_id(reference) == VIDEO


@pytest.mark.parametrize('reference', [
    'http://youtu.be/' + VIDEO,
    'https://evil.test/' + VIDEO,
    'https://youtube.com/watch?v=' + VIDEO + '&v=bbbbbbbbbbb',
    'https://user@youtube.com/watch?v=' + VIDEO,
])
def test_unsafe_references(reference, state):
    engine, fake = collector(state, [video()])
    with pytest.raises(ValueError):
        engine.collect(CollectionRequest(videos=[reference]))
    assert not fake.calls


@pytest.mark.parametrize(('reference', 'expected'), [
    (CHANNEL, ('id', CHANNEL)),
    ('https://youtube.com/channel/' + CHANNEL, ('id', CHANNEL)),
    ('https://youtube.com/@example/videos', ('forHandle', '@example')),
    ('https://youtube.com/user/example', ('forUsername', 'example')),
])
def test_channel_references(reference, expected):
    assert channel_reference(reference) == expected


@pytest.mark.parametrize('kwargs', [
    {'query': ' '},
    {'query': 'q', 'pages': 6},
    {'query': 'q', 'kind': 'channel', 'duration': 'short'},
    {'query': 'q', 'region': 'br'},
    {'query': 'q', 'published_after': '2026-01-01'},
])
def test_invalid_search(kwargs):
    with pytest.raises(ValidationError):
        SearchRequest(**kwargs)


def test_public_fields_missing_zero_and_hidden(state):
    rows = [video(views='0', likeCount='0')]
    engine, fake = collector(state, rows)
    result = engine.collect(CollectionRequest(videos=[VIDEO]))
    item = result.records[0]
    assert result.status == 'complete'
    assert item.views == item.likes == 0 and item.comments is None
    assert item.duration_seconds == 90 and item.published_at.tzinfo
    assert item.unavailable['comments'] == 'not_returned_or_invalid'
    assert item.unavailable['retention'] == 'not_public'
    assert result.records[1].subscribers_rounded is True
    assert Dataset.model_validate_json(result.model_dump_json()) == result
    assert [resource for resource, _ in fake.calls] == ['videos', 'channels']


def test_hidden_subscribers(state):
    row = channel()
    row['statistics']['hiddenSubscriberCount'] = True
    fake = Fake(lambda *_: {'items': [row]})
    item = YouTubeCollector(fake, state).collect(CollectionRequest(channels=[CHANNEL])).records[0]
    assert item.subscribers is None and item.unavailable['subscribers'] == 'not_public'


@pytest.mark.parametrize(('value', 'expected'), [
    ('PT0S', 0), ('P1DT2H3M4S', 93784), ('P', None), ('invalid', None), (None, None),
])
def test_duration(value, expected):
    assert duration(value) == expected


@pytest.mark.parametrize('value', [-1, True, 'nan', '1.5', 2**64, None])
def test_invalid_counts(value):
    assert number(value) is None


def test_keyword_discovery_pagination_and_cache(state):
    second = 'bbbbbbbbbbb'

    def reply(resource, params):
        if resource == 'search':
            assert params['relevanceLanguage'] == 'es' and params['regionCode'] == 'BR'
            assert params['publishedAfter'].startswith('2026-01-01')
            return {
                'items': [{'id': {'videoId': second if 'pageToken' in params else VIDEO}}],
                **({} if 'pageToken' in params else {'nextPageToken': 'next'}),
            }
        return {'items': [video(identifier) for identifier in params['id'].split(',')]}

    fake = Fake(reply)
    engine = YouTubeCollector(fake, state)
    request = CollectionRequest(
        search=SearchRequest(
            query='astronomia', language='es', region='BR', pages=2, published_after='2026-01-01T00:00:00Z'
        ),
        enrich_channels=False,
    )
    result = engine.collect(request)
    assert len(result.records) == 2 and result.quota_spent == {'search': 2, 'details': 1}
    again = engine.collect(request)
    assert len(fake.calls) == 3 and again.quota_spent == {'search': 0, 'details': 0}
    assert again.records[0].retrieved_at == result.records[0].retrieved_at


def test_channels_and_upload_discovery(state):
    def reply(resource, params):
        return {
            'items': {
                'search': [{'id': {'channelId': CHANNEL}}],
                'channels': [channel()],
                'playlistItems': [{'contentDetails': {'videoId': VIDEO}}],
                'videos': [video()],
            }[resource]
        }

    fake = Fake(reply)
    result = YouTubeCollector(fake, state).collect(
        CollectionRequest(search=SearchRequest(query='natureza', kind='channel'), upload_pages=1)
    )
    assert len(result.records) == 2
    assert [resource for resource, _ in fake.calls] == ['search', 'channels', 'playlistItems', 'videos']


@pytest.mark.parametrize(('pages', 'code'), [(1, 'pagination_limited'), (3, 'pagination_loop')])
def test_pagination_bounds(state, pages, code):
    fake = Fake(lambda *_: {'items': [], 'nextPageToken': 'same'})
    result = YouTubeCollector(fake, state).collect(CollectionRequest(search=SearchRequest(query='q', pages=pages)))
    assert result.issues[0].code == code and len(fake.calls) <= 2


def test_missing_resource_refresh_invalidates_old_copy(state):
    engine, fake = collector(state, [video()])
    original = engine.collect(CollectionRequest(videos=[VIDEO], enrich_channels=False))
    fake.handler = lambda *_: {'items': []}
    refreshed = engine.refresh(original.snapshot_id, 1)
    assert refreshed.revision == 2 and refreshed.status == 'failed'
    assert refreshed.issues[0].code == 'not_returned'
    with pytest.raises(KeyError):
        state.load(original.snapshot_id, 1)


def test_retry_charged_and_bounded(state):
    def fail(*_):
        raise CollectionError('transport', 'Falhou', retryable=True)

    fake = Fake(fail)
    result = YouTubeCollector(fake, state, sleep=lambda _: None).collect(CollectionRequest(videos=[VIDEO]))
    assert len(fake.calls) == 2 and result.quota_spent['details'] == 2
    assert result.status == 'failed' and result.issues[0].retryable


def test_upstream_quota_blocks_later_collections(state):
    def fail(*_):
        raise CollectionError('upstream_quota', 'Cota', quota=True)

    fake = Fake(fail)
    engine = YouTubeCollector(fake, state)
    request = CollectionRequest(videos=[VIDEO])
    assert engine.collect(request).issues[0].code == 'upstream_quota'
    assert engine.collect(request).issues[0].code == 'local_quota'
    assert len(fake.calls) == 1


@pytest.mark.pg_strong
def test_quota_atomic_persistent_and_separate_concurrent_sessions(pg_cluster, pg_pool):
    with pg_pool.connection() as conn:
        dbname = conn.info.dbname

    # Duas sessões/pools independentes disputando o mesmo limite de cota sem overspend.
    from psycopg_pool import ConnectionPool
    dsn = pg_cluster.dsn(dbname, 'horus_app')
    now = [datetime(2026, 11, 11, 23, 50, tzinfo=timezone(timedelta(hours=-3)))]
    with ConnectionPool(dsn, min_size=1, max_size=4, open=True, kwargs={'autocommit': True}) as pool_a, \
         ConnectionPool(dsn, min_size=1, max_size=4, open=True, kwargs={'autocommit': True}) as pool_b:
        state_a = RadarState(pool_a, clock=lambda: now[0])
        state_b = RadarState(pool_b, clock=lambda: now[0])

        def reserve(idx):
            target = state_a if idx % 2 == 0 else state_b
            try:
                target.reserve('search', 2)
                return True
            except BudgetExceeded:
                return False

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert sum(executor.map(reserve, range(8))) == 2
        state_a.reserve('details', 1)
        with pytest.raises(BudgetExceeded):
            state_b.reserve('search', 2)
        # Avança apenas 2 horas (cruza meia-noite UTC, mas ainda dentro da janela de 26h).
        now[0] = now[0] + timedelta(hours=2)
        with pytest.raises(BudgetExceeded):
            state_a.reserve('search', 2)
        # Após 26h completas em UTC, a janela libera.
        now[0] = now[0] + timedelta(hours=24)
        state_b.reserve('search', 2)


def test_per_collection_budget(state):
    engine, fake = collector(state, [video()], max_calls=1)
    result = engine.collect(CollectionRequest(videos=[VIDEO]))
    assert result.status == 'partial' and result.issues[0].code == 'local_quota'
    assert len(fake.calls) == 1


def test_cache_projection_expiry_and_dataset_revisions_and_listing(state):
    row = video()
    row['statistics']['privateMetric'] = 'secret'
    row['snippet']['description'] = 'not needed'
    engine, _ = collector(state, [row])
    request = CollectionRequest(videos=[VIDEO], enrich_channels=False)
    first = engine.collect(request)
    with state.connect() as db:
        payload = db.execute('SELECT payload FROM horus.radar_cache').fetchone()[0]
        serialized = json.dumps(payload, ensure_ascii=False)
        assert 'privateMetric' not in serialized and 'description' not in serialized
    with pytest.raises(psycopg.IntegrityError):
        state.save(first)
    state.advance(hours=7)
    newer = engine.refresh(first.snapshot_id, 1)
    assert newer.revision == 2 and newer.records[0].retrieved_at > first.records[0].retrieved_at
    listed = state.list_datasets()
    assert len(listed) == 1 and listed[0].revision == 2
    state.advance(days=29)
    with pytest.raises(KeyError):
        state.load(first.snapshot_id, 2)
    with state.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM horus.radar_cache').fetchone()[0] == 0


def test_indicators_require_permission_and_exclude_self(state):
    rows = [video(str(index).zfill(11), views='1000' if index == 0 else '10') for index in range(6)]
    engine, _ = collector(state, rows)
    dataset = engine.collect(CollectionRequest(videos=[row['id'] for row in rows], enrich_channels=False))
    blocked = opportunity_indicators(dataset, clock=state.clock)
    assert blocked['status'] == 'blocked_policy' and blocked['metrics'] == []
    with pytest.raises(ValidationError):
        MetricsPermission(approved=True)
    report = opportunity_indicators(dataset, MetricsPermission(approved=True, approval_reference='mock-only'), clock=state.clock)
    candidate = report['metrics'][0]
    assert candidate['ratio_to_sample_median'] == 100 and candidate['peer_count'] == 5
    assert candidate['source_id'] not in candidate['peer_source_ids'] and candidate['possible_outlier']
    state.advance(days=29)
    with pytest.raises(ValueError):
        opportunity_indicators(dataset, clock=state.clock)


def test_zero_baseline_is_not_outlier(state):
    rows = [video(str(index).zfill(11), views='100' if index == 0 else '0') for index in range(6)]
    engine, _ = collector(state, rows)
    dataset = engine.collect(CollectionRequest(videos=[row['id'] for row in rows], enrich_channels=False))
    report = opportunity_indicators(dataset, MetricsPermission(approved=True, approval_reference='mock'), clock=state.clock)
    assert not any(row['source_id'] == dataset.records[0].source_id for row in report['metrics'])


def test_transport_default_offline():
    with pytest.raises(CollectionError, match='Rede desabilitada'):
        GoogleTransport('mock-key').get('videos', {})
    assert NoRedirect().redirect_request(None, None, None, None, None, None) is None


@pytest.mark.parametrize(('status', 'reason', 'code', 'retry'), [
    (403, 'quotaExceeded', 'upstream_quota', False),
    (403, 'forbidden', 'http_403', False),
    (404, 'notFound', 'http_404', False),
    (429, 'rateLimitExceeded', 'rate_limit', True),
    (503, 'backendError', 'http_503', True),
])
def test_transport_http_errors_sanitized(monkeypatch, status, reason, code, retry):
    from horus_backend.radar_youtube import transport

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                'https://example/?key=mock-sensitive',
                status,
                'secret',
                {},
                BytesIO(json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()),
            )

    monkeypatch.setattr(transport, 'build_opener', lambda *_: Opener())
    with pytest.raises(CollectionError) as caught:
        GoogleTransport('mock-sensitive', allow_network=True).get('videos', {})
    assert caught.value.code == code and caught.value.retryable == retry
    assert 'mock-sensitive' not in str(caught.value)


def test_malformed_response_becomes_dataset_issue(state):
    fake = Fake(lambda *_: {'unexpected': []})
    result = YouTubeCollector(fake, state).collect(CollectionRequest(videos=[VIDEO]))
    assert result.status == 'failed' and result.issues[0].code == 'invalid_response'


def test_detail_batches_at_most_fifty_and_deduplicates(state):
    identifiers = [str(index).zfill(11) for index in range(51)]

    def reply(resource, params):
        if resource == 'search':
            ids = identifiers[50:] + [identifiers[0]] if 'pageToken' in params else identifiers[:50]
            return {
                'items': [{'id': {'videoId': identifier}} for identifier in ids],
                **({} if 'pageToken' in params else {'nextPageToken': 'next'}),
            }
        batch = params['id'].split(',')
        assert len(batch) <= 50
        return {'items': [video(identifier) for identifier in batch]}

    fake = Fake(reply)
    dataset = YouTubeCollector(fake, state).collect(
        CollectionRequest(search=SearchRequest(query='q', pages=2, page_size=50), enrich_channels=False)
    )
    assert len(dataset.records) == 51 and dataset.quota_spent == {'search': 2, 'details': 2}


def test_sample_requires_enough_comparable_peers(state):
    rows = [video(str(index).zfill(11)) for index in range(6)]
    rows[-1]['snippet']['liveBroadcastContent'] = 'live'
    engine, _ = collector(state, rows)
    dataset = engine.collect(CollectionRequest(videos=[row['id'] for row in rows], enrich_channels=False))
    report = opportunity_indicators(dataset, MetricsPermission(approved=True, approval_reference='mock'), clock=state.clock)
    assert report['metrics'] == []


def test_cached_source_does_not_renew_dataset_retention(state):
    engine, _ = collector(state, [video()])
    request = CollectionRequest(videos=[VIDEO], enrich_channels=False)
    original = engine.collect(request)
    state.advance(hours=1)
    cached = engine.collect(request)
    assert cached.collected_at > original.collected_at
    assert cached.expires_at == original.expires_at


def test_cli_requires_opt_in_before_creating_state(tmp_path, monkeypatch):
    monkeypatch.delenv('YOUTUBE_API_KEY', raising=False)
    with pytest.raises(SystemExit) as caught:
        radar_cli_main(['--request', str(tmp_path / 'missing.json'), '--state-dir', str(tmp_path / 'state')])
    assert caught.value.code == 2 and not (tmp_path / 'state').exists()


def test_cli_fails_without_pg_connection_and_creates_no_sqlite_file(tmp_path, monkeypatch, capsys):
    req_file = tmp_path / 'req.json'
    req_file.write_text(CollectionRequest(videos=[VIDEO]).model_dump_json(), encoding='utf-8')
    monkeypatch.setenv('YOUTUBE_API_KEY', 'mock-key')

    class MemoryKeyring:
        def get_password(self, *_):
            return None

    monkeypatch.setattr(
        'horus_backend.radar_youtube.__main__.ConnectionVault',
        lambda d: ConnectionVault(d, MemoryKeyring()),
    )
    state_dir = tmp_path / 'state'
    with pytest.raises(SystemExit) as caught:
        radar_cli_main(['--request', str(req_file), '--state-dir', str(state_dir), '--allow-network'])
    assert caught.value.code == 3
    err = capsys.readouterr().err
    assert 'database_unavailable' in err
    assert not list(tmp_path.glob('*.sqlite3'))
    assert not state_dir.exists()


def test_cli_succeeds_against_pg_with_mocked_transport(tmp_path, pg_pool, monkeypatch, capsys):
    req_file = tmp_path / 'req.json'
    req_file.write_text(CollectionRequest(videos=[VIDEO], enrich_channels=False).model_dump_json(), encoding='utf-8')
    monkeypatch.setenv('YOUTUBE_API_KEY', 'mock-key')
    monkeypatch.setattr(
        'horus_backend.radar_youtube.__main__.GoogleTransport',
        lambda *a, **kw: Fake(lambda *_: {'items': [video()]}),
    )
    radar_cli_main(['--request', str(req_file), '--state-dir', str(tmp_path), '--allow-network'], db=pg_pool)
    out = capsys.readouterr().out
    parsed = Dataset.model_validate_json(out)
    assert parsed.records[0].youtube_id == VIDEO
    assert not list(tmp_path.glob('*.sqlite3'))


@pytest.mark.parametrize(('payload', 'code'), [
    (b'not-json', 'invalid_response'),
    (b'[]', 'invalid_response'),
    (b'x' * 2_000_001, 'response_too_large'),
])
def test_transport_invalid_or_oversized_response(monkeypatch, payload, code):
    from horus_backend.radar_youtube import transport

    class Opener:
        def open(self, *args, **kwargs):
            return BytesIO(payload)

    monkeypatch.setattr(transport, 'build_opener', lambda *_: Opener())
    with pytest.raises(CollectionError) as caught:
        GoogleTransport('mock', allow_network=True).get('videos', {})
    assert caught.value.code == code


@pytest.mark.parametrize('resource', ['https://evil.test/', 'captions', '../videos'])
def test_transport_restricts_resources(resource):
    with pytest.raises(ValueError):
        GoogleTransport('mock', allow_network=True).get(resource, {})


def test_transport_never_accepts_key_in_persisted_parameters():
    with pytest.raises(ValueError):
        GoogleTransport('mock', allow_network=True).get('videos', {'key': 'not-allowed'})


def test_explicit_handle_lookup_without_search(state):
    fake = Fake(lambda *_: {'items': [channel()]})
    result = YouTubeCollector(fake, state).collect(CollectionRequest(channels=['@example']))
    assert result.records[0].youtube_id == CHANNEL
    assert fake.calls[0][1]['forHandle'] == '@example' and len(fake.calls) == 1


@pytest.mark.pg_strong
@pytest.mark.parametrize('search', [False, True])
def test_concurrent_collectors_share_uncached_query(pg_pool, state, search):
    entered, release, second_started = Event(), Event(), Event()

    def handler(resource, params):
        entered.set()
        assert release.wait(5)
        return {'items': [{'id': {'videoId': VIDEO}}] if resource == 'search' else [video()]}

    fake = Fake(handler)
    request = (
        CollectionRequest(search=SearchRequest(query='teste'), enrich_channels=False)
        if search
        else CollectionRequest(videos=[VIDEO], enrich_channels=False)
    )
    other = RadarState(pg_pool, clock=state.clock)

    def second():
        second_started.set()
        return YouTubeCollector(fake, other).collect(request)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(YouTubeCollector(fake, state).collect, request)
        assert entered.wait(5)
        next_result = pool.submit(second)
        assert second_started.wait(5)
        release.set()
        results = [first.result(5), next_result.result(5)]
    assert len(fake.calls) == (2 if search else 1)
    assert sorted(result.quota_spent['details'] for result in results) == [0, 1]
    assert all(result.status == 'complete' for result in results)


@pytest.mark.pg_strong
def test_concurrent_refresh_returns_one_successor(pg_pool, state):
    engine, fake = collector(state, [video()])
    original = engine.collect(CollectionRequest(videos=[VIDEO], enrich_channels=False))
    entered, release, second_started = Event(), Event(), Event()

    def handler(resource, params):
        entered.set()
        assert release.wait(5)
        return {'items': [video(views='20')]}

    fake.handler = handler
    other = RadarState(pg_pool, clock=state.clock)

    def second():
        second_started.set()
        return YouTubeCollector(fake, other).refresh(original.snapshot_id, 1)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(engine.refresh, original.snapshot_id, 1)
        assert entered.wait(5)
        following = pool.submit(second)
        assert second_started.wait(5)
        release.set()
        results = [first.result(5), following.result(5)]
    assert results[0] == results[1]
    assert results[0].revision == 2
    assert len(fake.calls) == 2
    assert state.load(original.snapshot_id, 1) == original


@pytest.mark.parametrize('where', ['before', 'page', 'endpoint', 'retry', 'last'])
def test_cooperative_cancellation_keeps_reservations_without_dataset(state, where):
    cancelled = Event()
    request = CollectionRequest(videos=[VIDEO])
    if where == 'before':
        cancelled.set()
    if where == 'page':
        request = CollectionRequest(search=SearchRequest(query='teste', pages=2))

    def handler(resource, params):
        cancelled.set()
        if where == 'retry':
            raise CollectionError('transport', 'simulado', retryable=True)
        if resource == 'search':
            return {'items': [{'id': {'videoId': VIDEO}}], 'nextPageToken': 'next'}
        return {'items': [video()]}

    fake = Fake(handler)
    if where == 'last':
        request = CollectionRequest(videos=[VIDEO], enrich_channels=False)
    engine = YouTubeCollector(fake, state, sleep=lambda _: None)
    with pytest.raises(CollectionCancelled):
        engine.collect(request, cancel=cancelled.is_set)
    expected = 0 if where == 'before' else 1
    assert len(fake.calls) == expected
    with state.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM horus.radar_usage').fetchone()[0] == expected
        assert db.execute('SELECT COUNT(*) FROM horus.radar_datasets').fetchone()[0] == 0
    cancelled.clear()
    fake.handler = lambda resource, params: {'items': [video()] if resource == 'videos' else [channel()]}
    assert engine.collect(CollectionRequest(videos=[VIDEO])).status == 'complete'


def test_cancelled_refresh_preserves_previous_revision(state):
    engine, fake = collector(state, [video()])
    original = engine.collect(CollectionRequest(videos=[VIDEO], enrich_channels=False))
    cancelled = Event()

    def handler(resource, params):
        cancelled.set()
        return {'items': [video(views='20')]}

    fake.handler = handler
    with pytest.raises(CollectionCancelled):
        engine.refresh(original.snapshot_id, 1, cancel=cancelled.is_set)
    assert state.load(original.snapshot_id, 1) == original
    with pytest.raises(KeyError):
        state.load(original.snapshot_id, 2)
    with state.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM horus.radar_usage').fetchone()[0] == 2


@pytest.mark.pg_strong
def test_cancel_while_waiting_for_collection_lock(pg_pool, state):
    cancelled, waiting = Event(), Event()
    contender = RadarState(pg_pool, clock=state.clock)
    engine, fake = collector(contender, [video()])

    def cancel():
        waiting.set()
        return cancelled.is_set()

    with state.collection_lock():
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(engine.collect, CollectionRequest(videos=[VIDEO]), cancel=cancel)
            assert waiting.wait(5)
            cancelled.set()
            with pytest.raises(CollectionCancelled):
                future.result(5)
    assert not fake.calls


@pytest.mark.pg_strong
@pytest.mark.parametrize('timeout', [0, 0.1])
def test_stuck_collection_lock_times_out_without_quota_or_partial_revision(pg_pool, state, timeout):
    contender = RadarState(pg_pool, clock=state.clock, lock_timeout=timeout)
    engine, fake = collector(contender, [video()])
    with state.collection_lock():
        started = monotonic()
        with pytest.raises(CollectionLockTimeout, match='trava do Radar'):
            engine.collect(CollectionRequest(videos=[VIDEO]))
        assert monotonic() - started < 1
    assert not fake.calls
    with state.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM horus.radar_usage').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM horus.radar_datasets').fetchone()[0] == 0
    assert engine.collect(CollectionRequest(videos=[VIDEO])).status == 'complete'


@pytest.mark.parametrize('timeout', [-1, float('inf'), float('nan')])
def test_collection_lock_rejects_unbounded_or_negative_timeout(pg_pool, timeout):
    with pytest.raises(ValueError, match='Timeout da trava'):
        RadarState(pg_pool, lock_timeout=timeout)


def test_cleanup_exact_expiry_preserves_live_revisions_and_quota_window(state):
    engine, _ = collector(state, [video()])
    original = engine.collect(CollectionRequest(videos=[VIDEO], enrich_channels=False))
    state.advance(days=1)
    successor = engine.refresh(original.snapshot_id, 1)
    state.advance(days=27)
    state.cache('live', {'items': []}, state.clock(), 1)
    state.reserve('search', 10)
    state.block('search')
    state.purge()
    state.purge()
    with pytest.raises(KeyError):
        state.load(original.snapshot_id, 1)
    assert state.load(successor.snapshot_id, 2) == successor
    assert state.cached('live') is not None
    with state.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM horus.radar_datasets').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM horus.radar_cache').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM horus.radar_usage').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM horus.radar_blocks').fetchone()[0] == 1
    state.advance(days=1, hours=2)
    state.purge()
    with state.connect() as db:
        for table in ['horus.radar_datasets', 'horus.radar_cache', 'horus.radar_usage', 'horus.radar_blocks']:
            assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0


@pytest.mark.pg_strong
def test_collection_lock_excludes_other_process_and_releases(pg_cluster, pg_pool, state):
    with pg_pool.connection() as conn:
        dbname = conn.info.dbname
    dsn = pg_cluster.dsn(dbname, 'horus_app')
    code = '''
import sys
from psycopg_pool import ConnectionPool
from horus_backend.radar_youtube.state import RadarState, CollectionCancelled
pool = ConnectionPool(sys.argv[1], min_size=1, max_size=2, kwargs={"autocommit": True})
state = RadarState(pool)
checks = 0
def cancel():
    global checks
    checks += 1
    return checks > 2
try:
    with state.collection_lock(cancel):
        print("acquired")
except CollectionCancelled:
    print("blocked")
finally:
    pool.close()
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}

    def run():
        return subprocess.run(
            [sys.executable, '-c', code, dsn],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()

    with state.collection_lock():
        assert run() == 'blocked'
    assert run() == 'acquired'


def test_refresh_of_invalidated_origin_returns_existing_successor(state):
    engine, fake = collector(state, [video()])
    original = engine.collect(CollectionRequest(videos=[VIDEO], enrich_channels=False))
    fake.handler = lambda resource, params: {'items': []}
    successor = engine.refresh(original.snapshot_id, 1)
    assert successor.status == 'failed'
    assert engine.refresh(original.snapshot_id, 1) == successor
    assert len(fake.calls) == 2
