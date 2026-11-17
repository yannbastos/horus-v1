import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { compare, synchronize } from './product-version.mjs';

test('SemVer: ordem, prerelease, metadados e entrada inválida', () => {
  for (const v of ['01.0.0', '1.0', 'v1.0.0', '1.0.0-01', '../1.0.0']) assert.throws(() => compare(v, '1.0.0'));
  for (const [a,b] of [['1.0.0-alpha.2','1.0.0-alpha.10'], ['1.0.0-rc.1','1.0.0'], ['1.9.0','1.10.0']]) assert.equal(compare(a,b), -1);
  assert.equal(compare('1.0.0+build.2', '1.0.0+build.3'), 0);
});

test('sincroniza cópia isolada, verifica divergência e recusa downgrade sem flag', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'horus-version-'));
  try {
    for (const [file, content] of Object.entries({
      'frontend/package.json': '{"version":"1.0.0"}',
      'frontend/src-tauri/tauri.conf.json': '{"version":"1.0.0"}',
      'frontend/src-tauri/Cargo.toml': '[package]\nversion = "1.0.0"\n',
      'backend/pyproject.toml': '[project]\nversion = "1.0.0"\n',
      'scripts/tauri-test.json': '{"version":"1.1.0"}',
      'frontend/package-lock.json': '{"name": "horus-desktop", "version": "1.0.0", "packages": {"": {"name": "horus-desktop", "version": "1.0.0"}, "node_modules/fixture": {"version": "5.0.0"}}}',
      'frontend/src-tauri/Cargo.lock': '[[package]]\nname = "horus-desktop"\nversion = "1.0.0"\n\n[[package]]\nname = "fixture"\nversion = "5.0.0"\n',
      'backend/uv.lock': '[[package]]\nname = "horus-backend"\nversion = "1.0.0"\n',
    })) { fs.mkdirSync(path.dirname(path.join(root,file)), {recursive:true}); fs.writeFileSync(path.join(root,file),content); }
    assert.throws(() => synchronize(root), /dessincronizadas/);
    assert.throws(() => synchronize(root, '1.0.0'), /menor ou igual/);
    assert.throws(() => synchronize(root, '0.9.0'), /menor ou igual/);
    assert.equal(synchronize(root,'1.2.0'), '1.2.0');
    assert.equal(synchronize(root), '1.2.0');
    const lock = JSON.parse(fs.readFileSync(path.join(root,'frontend/package-lock.json')));
    assert.equal(lock.packages[''].version,'1.2.0');
    assert.equal(lock.packages['node_modules/fixture'].version,'5.0.0');
    synchronize(root, '1.0.0', true);
    assert.equal(synchronize(root), '1.0.0');
    assert.throws(() => synchronize(root, '../invalid', true), /inválida/);
    assert.equal(synchronize(root), '1.0.0');
  } finally { fs.rmSync(root, {recursive:true}); }
});
