"""Suporte ao isolamento do cofre de credenciais nos testes do backend."""
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def _real_backend_classes():
    classes = []
    for module, name in (('keyring.backends.Windows', 'WinVaultKeyring'), ('keyring.backends.macOS', 'Keyring'),
                         ('keyring.backends.SecretService', 'Keyring'), ('keyring.backends.kwallet', 'DBusKeyring')):
        try:
            classes.append(getattr(__import__(module, fromlist=[name]), name))
        except Exception:      # backend indisponível nesta plataforma
            pass
    return classes
