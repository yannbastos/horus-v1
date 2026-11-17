"""Uma entrada versionada e indivisível no cofre nativo; sem fallback."""
import hashlib
import hmac
import json
from pathlib import Path

from .models import ConnectionRequest

SERVICE = "horus.database.connection.v1"


class VaultUnavailable(Exception):
    def __init__(self):
        super().__init__("database_vault_unavailable")


class ConnectionVault:
    def __init__(self, data_dir: Path, backend=None):
        self.account = hashlib.sha256(str(data_dir.resolve()).encode()).hexdigest()
        self._backend = backend

    def backend(self):
        if self._backend is not None:
            return self._backend  # injeção interna do harness, nunca HTTP/env
        import keyring
        backend = keyring.get_keyring()
        # Não aceitar plaintext, fail/null, chainer ou plugins não revisados.
        native = {("keyring.backends.Windows", "WinVaultKeyring"),
                  ("keyring.backends.macOS", "Keyring"),
                  ("keyring.backends.SecretService", "Keyring")}
        if (type(backend).__module__, type(backend).__name__) not in native or backend.priority <= 0:
            raise VaultUnavailable()
        return backend

    def read(self):
        try:
            raw = self.backend().get_password(SERVICE, self.account)
            if raw is None:
                return None
            if len(raw) > 32768:
                raise VaultUnavailable()
            document = json.loads(raw)
            if set(document) != {"version", "connection"} or document["version"] != 1:
                raise VaultUnavailable()
            return ConnectionRequest.model_validate(document["connection"])
        except Exception:
            raise VaultUnavailable() from None

    def write(self, config: ConnectionRequest):
        try:
            data = config.model_dump(mode="json")
            data["password"] = config.password.get_secret_value()
            raw = json.dumps({"version": 1, "connection": data}, ensure_ascii=False)
            backend = self.backend()
            backend.set_password(SERVICE, self.account, raw)
            verified = backend.get_password(SERVICE, self.account)
            if verified is None or not hmac.compare_digest(raw.encode(), verified.encode()):
                raise VaultUnavailable()
        except Exception:
            raise VaultUnavailable() from None

    def delete(self):
        try:
            backend = self.backend()
            if backend.get_password(SERVICE, self.account) is not None:
                backend.delete_password(SERVICE, self.account)
            if backend.get_password(SERVICE, self.account) is not None:
                raise VaultUnavailable()
        except Exception:
            raise VaultUnavailable() from None
