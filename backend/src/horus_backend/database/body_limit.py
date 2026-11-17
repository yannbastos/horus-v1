"""Teto ASGI antes de JSON/Pydantic, preservando o envelope do diagnóstico."""
import json

from ..body_limit import BodyLimitMiddleware


class _DiagnosticLimit(BodyLimitMiddleware):
    def __init__(self, app, manager):
        super().__init__(app, default=16*1024, overrides=())
        self.manager = manager

    async def _reject(self, send, limit):
        body = json.dumps({"detail": {"code": "database_invalid_connection",
            "message": "Configuração de conexão inválida.",
            "status": self.manager.snapshot.model_dump()}}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": 422, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})


class DatabaseBodyLimitMiddleware(BodyLimitMiddleware):
    def __init__(self, app, manager):
        super().__init__(app)
        self.database_app = _DiagnosticLimit(app, manager)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/v1/database/"):
            return await self.database_app(scope, receive, send)
        return await super().__call__(scope, receive, send)
