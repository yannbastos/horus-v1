"""Barreiras de licença para vozes e assets do piloto (HOR-097 / HOR-132). Offline; sem rede, sem síntese."""
import copy

import pytest

from horus_backend.av_quality import provenance_check
from horus_backend.media_contracts import ProvenanceAsset, apply_license_reviews, commercial_use_permitted, validate_asset_provenance
from horus_backend.narration import providers
from horus_backend.narration.kokoro import Kokoro, NOTES
from horus_backend.publishing import licenses_pending

PREFIX = 'canais/demo/videos/fixture/'
SHA = {'narration': '1' * 64, 'scene-1': '2' * 64, 'subtitle-font': '3' * 64}
UNSET = {'id': None, 'name': None, 'url': None, 'commercial_use': 'unknown', 'attribution_required': None,
         'verified_by': None, 'verified_at': None}
HUMAN = {'id': 'x', 'name': 'Termos X', 'url': 'https://example.org/termos', 'commercial_use': 'allowed',
         'attribution_required': False, 'verified_by': 'human', 'verified_at': '2026-11-14T10:00:00+00:00'}


def asset(asset_id, origin, kind, **over):
    value = {'asset_id': asset_id, 'relative_path': PREFIX + asset_id + '.bin', 'sha256': SHA[asset_id], 'kind': kind,
             'origin': origin, 'source_url': None, 'author': None, 'license': dict(UNSET), 'status': 'unverified', 'notes': ''}
    return value | over


def document(voice_origin='local_tts_model'):
    return {'schema_version': 1, 'assets': [asset('narration', voice_origin, 'voice'), asset('scene-1', 'original_local', 'image')]}


def verified(entry, **license_over):
    return entry | {'status': 'verified', 'license': {**HUMAN, **license_over}}


# --- vozes: todo motor declara licença com fonte e nenhum afirma uso comercial -------------------------------

@pytest.mark.parametrize('cls', [providers.WindowsSapi, providers.Piper, providers.ElevenLabs, Kokoro])
def test_every_engine_declares_a_license_with_a_source_and_never_claims_commercial_use(cls):
    lic = cls.license
    assert lic['name'] and lic['url'].startswith('https://') and lic['notes']
    assert lic.get('commercial_use') != 'allowed'


def test_engine_license_text_is_honest_about_unverified_voice_rights():
    assert 'verificada' in providers.WindowsSapi.license['notes']       # voz do Windows: licença separada do motor
    assert 'própria' in providers.Piper.license['notes']                # cada modelo Piper tem licença própria
    assert 'unknown' in providers.ElevenLabs.license['notes']
    assert 'não verificados' in NOTES and 'CC BY' in NOTES               # Kokoro: pesos Apache-2.0, áudio de treino não


def test_default_voice_record_is_never_commercial():
    item = providers.voice('v', 'Voz', 'pt-BR')
    assert item['commercial_use'] == 'unknown' and item['license']['name'] == 'unknown'


def test_every_production_engine_maps_to_a_voice_origin_that_needs_human_review():
    assert set(providers.PRODUCTION_ORIGINS.values()) <= {'system_voice', 'local_tts_model'}
    for origin in providers.PRODUCTION_ORIGINS.values():
        data = document(origin)
        assert 'unverified_assets_block_publication' in validate_asset_provenance(data)['warnings']
        assert not any(commercial_use_permitted(ProvenanceAsset.model_validate(a)) for a in data['assets'])


# --- matriz negativa: nada libera uso comercial sem humano + allowed ---------------------------------------

@pytest.mark.parametrize('origin', ['system_voice', 'local_tts_model', 'original_local', 'licensed_manual'])
@pytest.mark.parametrize('status,commercial,human', [
    ('unverified', 'allowed', True),     # nunca revisado
    ('rejected', 'allowed', True),       # recusado
    ('verified', 'restricted', True),    # lida, mas restrita
    ('verified', 'allowed', False),      # sem humano
])
def test_commercial_use_matrix_negative(origin, status, commercial, human):
    lic = {**HUMAN, 'commercial_use': commercial}
    if not human:
        lic.update(verified_by=None, verified_at=None)
    entry = asset('narration', origin, 'voice', source_url='https://example.org/x', author='A', status=status, license=lic)
    try:
        model = ProvenanceAsset.model_validate(entry)
    except ValueError:        # combinação já recusada pelo contrato (ex.: verified sem humano)
        return
    assert commercial_use_permitted(model) is False


def test_only_human_verified_allowed_is_permitted():
    entry = verified(asset('narration', 'local_tts_model', 'voice'))
    assert commercial_use_permitted(ProvenanceAsset.model_validate(entry)) is True


# --- aviso de publicação, QC e divulgação ------------------------------------------------------------------

def test_one_unverified_voice_keeps_qc_warn_and_disclosure_pending():
    data = document()
    data['assets'][1] = verified(data['assets'][1])         # cena revisada; voz não
    check = provenance_check(data, {a['relative_path']: a['sha256'] for a in data['assets']})
    assert check['status'] == 'warn'
    assert licenses_pending(data) is True


def test_all_verified_and_allowed_is_the_only_state_not_pending():
    data = document()
    data['assets'] = [verified(a) for a in data['assets']]
    check = provenance_check(data, {a['relative_path']: a['sha256'] for a in data['assets']})
    assert check['status'] == 'pass'
    assert licenses_pending(data) is False


def test_missing_provenance_is_pending():
    assert licenses_pending(None) is True


def test_verified_but_restricted_voice_stays_pending():
    """`verified` + `restricted` não pode virar 'Licenças revisadas' nos metadados."""
    data = document()
    data['assets'] = [verified(a) for a in data['assets']]
    data['assets'][0]['license']['commercial_use'] = 'restricted'
    assert licenses_pending(data) is True


def test_review_cannot_reuse_a_decision_for_a_replaced_voice_file():
    """Voz regenerada (novo hash) não herda a aprovação da anterior."""
    data = document()
    review = {'asset_id': 'narration', 'asset_sha256': '9' * 64, 'decision': 'verified',
              'reviewed_at': '2026-11-14T10:00:00+00:00',
              'license': {k: HUMAN[k] for k in ('id', 'name', 'url', 'commercial_use', 'attribution_required')}}
    effective = apply_license_reviews(copy.deepcopy(data), [review])
    assert effective['assets'][0]['status'] == 'unverified' and licenses_pending(effective) is True


@pytest.mark.xfail(strict=True, reason="Lacuna conhecida: av_quality.provenance_check (fora do escopo desta entrega) trata "
                                       "verified+restricted como 'pass'; a divulgação de publishing já fica pendente.")
def test_qc_should_warn_for_verified_but_restricted_license():
    data = document()
    data['assets'] = [verified(a) for a in data['assets']]
    data['assets'][0]['license']['commercial_use'] = 'restricted'
    assert provenance_check(data, {a['relative_path']: a['sha256'] for a in data['assets']})['status'] == 'warn'
