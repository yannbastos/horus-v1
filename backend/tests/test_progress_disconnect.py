"""Streaming ASGI sem sockets: desconectar o observador não cancela o job."""
import asyncio
import json

import pytest
from starlette.requests import ClientDisconnect, Request

from horus_backend.api import create_app
from horus_backend.worker_lock import WorkerLock
from support_a import settings  # noqa: F401
from support_f import pg_manager, pg_store  # noqa: F401


async def connected_app(settings):
    """`create_app` em modo conectado: o lifespan não roda aqui, então abre a conexão PostgreSQL como ele faz."""
    app = create_app(settings)
    await asyncio.to_thread(app.state.database.test)
    assert app.state.database.snapshot.state == 'connected'
    return app


@pytest.mark.parametrize('waiting', [False, True])
def test_disconnect_releases_progress_stream_and_preserves_job(settings, waiting):
    async def run():
        app = await connected_app(settings)
        store, manager = app.state.store, app.state.manager
        job, _ = store.create_job({'operation': 'factory.profile', 'channel_id': 'fixture'}, 'disconnect')
        store.update(job['id'], 'running')
        store.event(job['id'], 'stdout', 'progresso preservado')
        before = store.get_job(job['id'])
        persisted = store.events(job['id'], 0)
        disconnected, entered_wait = asyncio.Event(), asyncio.Event()

        class ObservedCondition(asyncio.Condition):
            async def wait(self):
                entered_wait.set()
                return await super().wait()

        manager.changed = ObservedCondition()
        frames = []

        async def receive():
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == 'http.response.body' and message.get('body'):
                frames.append(json.loads(message['body']))
                disconnected.set()
                await asyncio.sleep(0)

        endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/v1/jobs/{job_id}/events')
        scope = {'type': 'http', 'asgi': {'spec_version': '2.3'}}
        response = await endpoint(job['id'], Request(scope, receive), after=persisted[-1]['sequence'] if waiting else 0)

        async def disconnect_waiter():
            await entered_wait.wait()
            disconnected.set()

        trigger = asyncio.create_task(disconnect_waiter()) if waiting else None
        try:
            await asyncio.wait_for(response(scope, receive, send), 1)
        finally:
            if trigger:
                trigger.cancel()
                await asyncio.gather(trigger, return_exceptions=True)
        assert response.body_iterator.ag_frame is None
        assert store.get_job(job['id']) == before
        assert store.events(job['id'], 0) == persisted
        assert not any(frame['kind'] == 'complete' for frame in frames)
        assert bool(frames) is not waiting
        # Cancelar o await da Condition não pode deixar o mutex adquirido.
        await asyncio.wait_for(manager.changed.acquire(), .2)
        manager.changed.release()
        # Um novo observador pode retomar pelo cursor após a conclusão da tarefa.
        store.update(job['id'], 'succeeded')
        async def connected():
            await asyncio.Event().wait()
        resumed = await endpoint(job['id'], Request(scope, connected), after=persisted[-1]['sequence'])
        replay = [json.loads(frame) async for frame in resumed.body_iterator]
        assert replay[-1]['kind'] == 'complete'
        assert replay[-1]['job']['status'] == 'succeeded'

    asyncio.run(run())


def test_progress_send_failure_closes_generator(settings):
    async def run():
        app = await connected_app(settings)
        store = app.state.store
        job, _ = store.create_job({'operation': 'factory.profile', 'channel_id': 'fixture'}, 'broken-send')
        store.update(job['id'], 'running')
        store.event(job['id'], 'stdout', 'progresso')
        endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/v1/jobs/{job_id}/events')
        scope = {'type': 'http', 'asgi': {'spec_version': '2.4'}}
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('cliente desconectado durante envio')
        response = await endpoint(job['id'], Request(scope, receive))
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(response(scope, receive, send), 1)
        assert response.body_iterator.ag_frame is None
        assert store.get_job(job['id'])['status'] == 'running'
    asyncio.run(run())


@pytest.mark.parametrize('during', ['send', 'idle'])
def test_client_timeout_closes_progress_generator_and_releases_condition(settings, during):
    async def run():
        app = await connected_app(settings)
        store, manager = app.state.store, app.state.manager
        job, _ = store.create_job({'operation': 'factory.profile', 'channel_id': 'fixture'}, 'client-timeout')
        store.update(job['id'], 'running')
        if during == 'send':
            store.event(job['id'], 'stdout', 'progresso')
        before = store.get_job(job['id'])
        entered = asyncio.Event()
        class ObservedCondition(asyncio.Condition):
            async def wait(self):
                entered.set()
                return await super().wait()
        manager.changed = ObservedCondition()
        endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/v1/jobs/{job_id}/events')
        scope = {'type': 'http', 'asgi': {'spec_version': '2.3'}}
        async def receive():
            await asyncio.Event().wait()
        async def send(message):
            if message['type'] == 'http.response.body':
                entered.set()
                await asyncio.Event().wait()  # cliente não drena o frame em progresso
        response = await endpoint(job['id'], Request(scope, receive))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(response(scope, receive, send), .03)
        assert entered.is_set()
        assert response.body_iterator.ag_frame is None
        assert store.get_job(job['id']) == before
        await asyncio.wait_for(manager.changed.acquire(), .2)
        manager.changed.release()
    asyncio.run(run())


def test_worker_lock_contender_fails_immediately_and_can_retry(pg_store):
    # Mesmo banco PostgreSQL e mesmo dispositivo: a trava de lease/fencing recusa o segundo executor sem esperar.
    owner, contender = WorkerLock(pg_store), WorkerLock(pg_store)
    owner.acquire()
    try:
        for _ in range(3):
            with pytest.raises(RuntimeError, match='Outro backend'):
                contender.acquire()
            assert not contender.active and contender.fencing_token is None
    finally:
        owner.close()
    contender.acquire()
    assert contender.active
    contender.close()
