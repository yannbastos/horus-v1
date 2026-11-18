"""Contrato local de diagnóstico; validação sem eco de entradas privadas."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from .manager import DatabaseManager, DatabaseRefused, MESSAGES
from .models import ConnectionRequest, DatabaseErrorResponse, DatabaseStatus
from .vault import ConnectionVault

BODY_LIMIT = 16 * 1024


def error_response(manager, code, status):
    return JSONResponse({"detail": {"code": code, "message": MESSAGES.get(code, "Conexão com o banco indisponível."),
                                    "status": manager.snapshot.model_dump()}}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def payload(request, manager):
    data = bytearray()
    try:
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > BODY_LIMIT:
                raise DatabaseRefused("database_invalid_connection", 422)
    except ClientDisconnect:
        # O limitador ASGI interrompe o receive ao exceder o teto. Corpo
        # incompleto não é falha do banco e não pode fechar um pool válido.
        raise DatabaseRefused("database_invalid_connection", 422) from None
    return bytes(data)


def database_router(manager: DatabaseManager, *, close_on_shutdown: bool = True):
    @asynccontextmanager
    async def lifespan(app):
        # Sem configuração, não faz I/O de rede; o app sobe em diagnóstico.
        if manager.snapshot.state == "connecting":
            await asyncio.to_thread(manager.test)
        try:
            yield
        finally:
            # Dono do ciclo de vida (api.py) fecha o pool depois dos workers/etapas.
            if close_on_shutdown:
                await asyncio.to_thread(manager.close)

    router = APIRouter(prefix="/v1/database", tags=["database"], lifespan=lifespan)
    errors = {code: {"model": DatabaseErrorResponse} for code in (409, 422, 500, 503)}

    async def execute(request, action, *, connection=False, empty_object=False):
        try:
            raw = await payload(request, manager)
            if connection:
                try:
                    config = ConnectionRequest.model_validate_json(raw)
                except ValidationError as error:
                    code = "database_endpoint_not_allowed" if any(e["loc"] == ("host",) for e in error.errors(include_input=False)) else "database_invalid_connection"
                    raise DatabaseRefused(code, 422) from None
                return await asyncio.to_thread(action, config)
            if raw and not (empty_object and raw.strip() == b"{}"):
                raise DatabaseRefused("database_invalid_connection", 422)
            return await asyncio.to_thread(action)
        except DatabaseRefused as error:
            return error_response(manager, error.code, error.http_status)
        except Exception:
            await asyncio.to_thread(manager.close)
            manager._set("unreachable", "database_internal_error")
            return error_response(manager, "database_internal_error", 500)

    @router.get("/status", response_model=DatabaseStatus, responses=errors)
    async def database_status(request: Request):
        # Não relê cofre, não conecta, não muda checked_at.
        try:
            raw = await payload(request, manager)
        except DatabaseRefused:
            return error_response(manager, "database_invalid_connection", 422)
        if raw:
            return error_response(manager, "database_invalid_connection", 422)
        return JSONResponse(manager.snapshot.model_dump(), headers={"Cache-Control": "no-store"})

    @router.post("/connection", response_model=DatabaseStatus, responses=errors,
                 openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
                     "schema": ConnectionRequest.model_json_schema()}}}})
    async def database_connection(request: Request):
        result = await execute(request, manager.configure, connection=True)
        return result if isinstance(result, JSONResponse) else JSONResponse(result.model_dump(), headers={"Cache-Control": "no-store"})

    @router.delete("/connection", response_model=DatabaseStatus, responses=errors)
    async def database_disconnect(request: Request):
        result = await execute(request, manager.delete)
        return result if isinstance(result, JSONResponse) else JSONResponse(result.model_dump(), headers={"Cache-Control": "no-store"})

    @router.post("/test", response_model=DatabaseStatus, responses=errors)
    async def database_test(request: Request):
        result = await execute(request, manager.test, empty_object=True)
        return result if isinstance(result, JSONResponse) else JSONResponse(result.model_dump(), headers={"Cache-Control": "no-store"})

    return router


def install_database(app, data_dir, busy):
    manager = DatabaseManager(ConnectionVault(data_dir), busy=busy)
    app.state.database = manager
    app.include_router(database_router(manager, close_on_shutdown=False))
