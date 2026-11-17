"""Acesso PostgreSQL exclusivo; nenhum fallback ou import SQLite neste pacote."""
from .manager import DatabaseManager, DatabaseRefused
from .models import DatabaseStatus
from .router import install_database

__all__ = ["DatabaseManager", "DatabaseRefused", "DatabaseStatus", "install_database"]
