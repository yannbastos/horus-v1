"""Ferramenta verificável de migração SQLite -> PostgreSQL (HOR-164 I-mig)."""
from __future__ import annotations

from .mapping import MAPPING_VERSION
from .runner import run_migration

__all__ = ["MAPPING_VERSION", "run_migration"]
