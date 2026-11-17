"""DTOs públicos: nenhum descritor ou segredo aparece no diagnóstico."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .expected_schema import EXPECTED_SCHEMA_VERSION

DatabaseState = Literal["not_configured", "connecting", "connected", "unreachable", "auth_failed",
                        "schema_mismatch", "migrating", "vault_unavailable"]


class DatabaseStatus(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: DatabaseState
    latency_ms: float | None = Field(ge=0)
    schema_version: str | None
    expected_schema_version: str = Field(min_length=1)
    last_error_code: str | None
    host_label: str | None
    checked_at: str | None

    @classmethod
    def initial(cls):
        return cls(state="not_configured", latency_ms=None, schema_version=None,
                   expected_schema_version=EXPECTED_SCHEMA_VERSION, last_error_code=None,
                   host_label=None, checked_at=None)


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    host: str = Field(min_length=1, max_length=253)
    port: Literal[5432]
    database: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    user: str = Field(min_length=1, max_length=128, pattern=r"^horus_app\.[a-z0-9]{20}$")
    password: SecretStr = Field(min_length=1, max_length=4096, json_schema_extra={"writeOnly": True})

    @field_validator("host")
    @classmethod
    def hostname(cls, value):
        # ASCII/IDNA canônico; rejeita URI, IP, listas, caminhos e opções libpq.
        import re
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.pooler\.supabase\.com", value):
            raise ValueError("Destino não permitido")
        return value

    @field_validator("port", mode="before")
    @classmethod
    def integer_port(cls, value):
        if type(value) is not int or value != 5432:
            raise ValueError("Porta inválida")
        return value

    @field_validator("password")
    @classmethod
    def no_nul(cls, value):
        if "\x00" in value.get_secret_value():
            raise ValueError("Credencial inválida")
        return value


class DatabaseErrorBody(BaseModel):
    code: str
    message: str
    status: DatabaseStatus


class DatabaseErrorResponse(BaseModel):
    detail: DatabaseErrorBody
