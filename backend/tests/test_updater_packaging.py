import importlib.util
import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]


def test_test_channel_serves_only_manifest_and_versioned_artifacts(tmp_path):
    spec=importlib.util.spec_from_file_location('updater_server',ROOT/'scripts/serve-updater-test.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path/'latest.json').write_text('{"version":"1.0.0"}')
    (tmp_path/'private.key').write_text('synthetic-never-serve')
    (tmp_path/'1.0.0').mkdir()
    (tmp_path/'1.0.0/setup.exe').write_bytes(b'fixture')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(module.Handler,directory=str(tmp_path)))
    worker=threading.Thread(target=server.serve_forever)
    worker.start()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with opener.open(base+'/latest.json') as response: assert json.load(response)['version']=='1.0.0'
        with opener.open(base+'/1.0.0/setup.exe') as response: assert response.read()==b'fixture'
        for route in ['/','/private.key','/../private.key','/%2e%2e/private.key','/1.0.0/']:
            with pytest.raises(urllib.error.HTTPError) as error: opener.open(base+route)
            assert error.value.code==404
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5)


def test_signed_overlay_has_no_insecure_production_default():
    default=json.loads((ROOT/'frontend/src-tauri/tauri.conf.json').read_text())
    assert not default.get('plugins',{}).get('updater')
    script=(ROOT/'scripts/build-updater-test.ps1').read_text(encoding='utf-8')
    assert 'http://127.0.0.1:$Port/latest.json' in script
    assert "requireSignedVersion=$true" in script
    assert "installMode='passive'" in script
    assert 'signer generate --ci --write-keys $key *> $null' in script
    assert 'TAURI_SIGNING_PRIVATE_KEY=$oldKey' in script
