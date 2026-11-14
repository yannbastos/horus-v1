"""Redação de dados privados no envelope da etapa de análise (requisito S-4 da política de destinos). Offline.

O envelope de `script` já rejeita caminhos locais e credenciais (`test_script_stage.py`). O envelope de `analysis`
(`RunEnvelope`) ainda não: os testes marcados `xfail(strict=True)` documentam a lacuna e passam a falhar (acusando
o xfail) quando ela for corrigida em `hermes_contract/models.py`, momento de remover a marcação.
"""
import copy

import pytest
from pydantic import ValidationError

from conftest import load
from horus_backend.hermes_contract.models import RunEnvelope

PRIVATE = ["ver C:/Users/yann/roteiro.txt", "veja /home/yann/projeto", "file:///etc/passwd",
           "api_key = abc123", "Authorization: Bearer abcdefgh12345678", "chave sk-abcdefghijklmnopqrstuvwx"]
FREE_TEXT = [("objective",), ("feedback",), ("channel_profile", "summary")]


def build(path, value):
    data = copy.deepcopy(load("envelope-ficticio.json"))
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


def test_fixture_is_valid():
    RunEnvelope.model_validate(load("envelope-ficticio.json"))


@pytest.mark.parametrize("field", ["workspace_path", "api_key", "token", "other_projects", "script_text"])
def test_envelope_has_no_room_for_private_extra_fields(field):
    data = copy.deepcopy(load("envelope-ficticio.json"))
    data[field] = "x"
    with pytest.raises(ValidationError):
        RunEnvelope.model_validate(data)


@pytest.mark.parametrize("value", ["C:/Users/yann", "/home/yann/x"])
def test_identifier_fields_reject_paths_and_keys(value):
    for path in (("channel_profile", "channel_id"), ("task_id",), ("workflow_id",)):
        with pytest.raises(ValidationError):
            RunEnvelope.model_validate(build(path, value))


@pytest.mark.xfail(strict=True, reason="Lacuna S-4: texto livre do envelope de análise não rejeita caminho/credencial.")
@pytest.mark.parametrize("value", PRIVATE)
@pytest.mark.parametrize("path", FREE_TEXT)
def test_free_text_rejects_local_paths_and_credentials(path, value):
    with pytest.raises(ValidationError):
        RunEnvelope.model_validate(build(path, value))
