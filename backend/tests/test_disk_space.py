"""HOR-094: verificação de espaço em disco no diagnóstico de produção. Sem medir o disco real; nada é criado ou apagado."""
from collections import namedtuple
from pathlib import Path

import pytest

from horus_backend import diagnostics as diagnostics_module
from horus_backend.config import Settings
from horus_backend.disk_space import HEADROOM_FACTOR, PILOT_PRODUCTION_ESTIMATE_BYTES, REQUIRED_FREE_BYTES, disk_space_check

Usage = namedtuple("Usage", "total used free")
GIB = 1024 ** 3


@pytest.fixture
def settings(tmp_path):
    return Settings(token="disk-space-tests-token-0123456789", data_dir=tmp_path / "workspace" / "data")


def usage_of(free):
    return lambda path: Usage(10 * GIB, 10 * GIB - free, free)


def test_threshold_is_derived_from_the_measured_pilot_production():
    assert PILOT_PRODUCTION_ESTIMATE_BYTES == 512 * 1024 ** 2 and HEADROOM_FACTOR == 4 and REQUIRED_FREE_BYTES == 2 * GIB


def test_enough_space_is_available_and_reports_free_space_per_volume(settings):
    check = disk_space_check(settings, usage=usage_of(50 * GIB))
    assert check["id"] == "disk_space" and check["available"] is True and set(check["required_for"]) == {"draft", "final"}
    assert "50,0 GiB livres" in check["detail"] and "suficiente" in check["detail"]


@pytest.mark.parametrize("free", [0, 1, 512 * 1024 ** 2, 2 * GIB - 1])
def test_low_space_is_unavailable_with_an_actionable_message(settings, free):
    check = disk_space_check(settings, usage=usage_of(free))
    assert check["available"] is False
    assert "Espaço insuficiente" in check["detail"] and "Libere espaço" in check["detail"] and "HORUS_DATA_DIR" in check["detail"]
    assert "2,0 GiB" in check["detail"] and "Nada é apagado automaticamente" in check["detail"]


def test_exactly_the_threshold_passes(settings):
    assert disk_space_check(settings, usage=usage_of(2 * GIB))["available"] is True


def test_only_the_volume_that_is_low_is_blamed(settings, monkeypatch, tmp_path):
    import horus_backend.disk_space as module
    temp = tmp_path / "volume-b-x"
    temp.mkdir()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(temp))
    check = disk_space_check(settings, device=lambda p: 2 if "volume-b-x" in str(p) else 1,
                             usage=lambda p: Usage(10 * GIB, 0, 1 * GIB if "volume-b-x" in str(p) else 100 * GIB))
    assert check["available"] is False and "Espaço insuficiente em temporários" in check["detail"]


def test_messages_use_labels_never_absolute_paths(settings, tmp_path):
    check = disk_space_check(settings, usage=usage_of(0))
    assert str(tmp_path) not in check["detail"] and "workspace" not in check["detail"] and "\\" not in check["detail"]
    assert "pasta de dados" in check["detail"] and "temporários" in check["detail"]


def test_check_never_creates_folders_even_if_the_data_dir_does_not_exist_yet(settings):
    assert not settings.data_dir.exists()
    disk_space_check(settings, usage=usage_of(50 * GIB))
    assert not settings.data_dir.exists() and not settings.data_dir.parent.exists()


def test_unmeasurable_volume_warns_without_blocking(settings):
    def boom(path):
        raise OSError("sem acesso")
    check = disk_space_check(settings, usage=boom)
    assert check["available"] is True and "Não foi possível medir" in check["detail"]


def test_diagnostics_includes_the_check_and_low_space_flips_readiness(settings, monkeypatch):
    report = diagnostics_module.production_diagnostics(settings)
    assert "disk_space" in [c["id"] for c in report["checks"]]
    monkeypatch.setattr(diagnostics_module, "disk_space_check", lambda s: disk_space_check(s, usage=usage_of(1)))
    low = diagnostics_module.production_diagnostics(settings)
    assert "disk_space" in low["missing"] and low["ready"] is False
