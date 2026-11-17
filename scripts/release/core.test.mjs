import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { releaseTag, guard, overlay, baseURL, candidate, productVersions, publish, assertUploadSize } from './core.mjs';
import { root } from './runtime.mjs';

test('tags aceitas e canais separados; SemVer/NSIS inválidos recusados', () => {
  assert.deepEqual(releaseTag('v1.2.3'), { version: '1.2.3', channel: 'production' });
  assert.deepEqual(releaseTag('v1.2.3-test.10'), { version: '1.2.3-test.10', channel: 'test' });
  for (const tag of ['1.2.3', 'v01.2.3', 'v1.2', 'v1.2.3-rc.1', 'v1.2.3-test.01', 'v1.2.3+build', 'v65536.0.0', 'v1.0.0-test.65536', 'v1.2.3\n']) assert.throws(() => releaseTag(tag), tag);
});
test('guarda de igualdade, duplicata, regressão, monotonia e canal', () => {
  const input = { tag: 'v1.2.3-test.10', versions: Array(3).fill('1.2.3-test.10'), latest: '1.2.3-test.9' };
  assert.equal(guard(input).channel, 'test');
  for (const change of [{ exists: true }, { versions: ['1.2.3', '1.2.3', '1.2.3'] }, { latest: '1.2.3-test.10' }, { latest: '1.3.0-test.1' }, { latest: '1.2.3' }, { latest: 'invalid' }]) assert.throws(() => guard({ ...input, ...change }));
  assert.equal(guard({ tag: 'v2.0.0', versions: Array(3).fill('2.0.0') }).channel, 'production');
  const versions = productVersions(root);
  assert.equal(versions.length, 3);
  assert.ok(versions.every(v => v === versions[0] && typeof v === 'string'));
});
test('overlay exige HTTPS e parâmetros; não aceita credenciais ou caminhos', () => {
  const env = { HORUS_UPDATE_CHANNEL: 'test', HORUS_UPDATE_BASE_URL: 'https://updates.example', HORUS_UPDATER_PUBKEY: 'public-test' };
  const result = overlay(env, JSON.parse(fs.readFileSync(path.join(root, 'frontend/src-tauri/tauri.release.conf.json'))));
  assert.deepEqual(result.plugins.updater.endpoints, ['https://updates.example/test/latest.json']);
  assert.equal(result.plugins.updater.requireSignedVersion, true);
  assert.equal(result.plugins.updater.dangerousInsecureTransportProtocol, false);
  assert.equal(result.plugins.horusUpdater.channel, 'test');
  assert.equal(result.bundle.createUpdaterArtifacts, true);
  assert.equal(result.bundle.windows.nsis.installMode, 'currentUser');
  assert.deepEqual(result.bundle.windows.webviewInstallMode, { type: 'downloadBootstrapper', silent: true });
  for (const value of ['http://updates.example', 'http://localhost', 'https://a:b@updates.example', 'https://updates.example/path', 'https://updates.example/?token=x']) assert.throws(() => baseURL(value));
  assert.equal(baseURL('http://127.0.0.1:1234', true), 'http://127.0.0.1:1234');
  for (const change of [{ HORUS_UPDATE_CHANNEL: 'prod' }, { HORUS_UPDATER_PUBKEY: '' }]) assert.throws(() => overlay({ ...env, ...change }, {}));
});

test('upload usa o limite S3, aceitando acima de 300 MiB sem depender do wrangler', () => {
  for (const bytes of [1, 300 * 1024 ** 2 + 1, 5 * 1024 ** 3]) assert.equal(assertUploadSize(bytes), bytes);
  for (const bytes of [0, -1, 5 * 1024 ** 3 + 1, NaN, 1.5]) assert.throws(() => assertUploadSize(bytes));
});
test('candidato contém layout e metadados completos, sem segredo', () => {
  const args = { tag: 'v1.2.3', installer: Buffer.from('synthetic'), signature: 'test-signature', origin: 'https://updates.example', notes: 'Notas', pub_date: '2026-11-17T00:00:00Z', commit: 'a'.repeat(40) };
  const result = candidate(args);
  assert.equal(result.latest.platforms['windows-x86_64'].url, 'https://updates.example/production/releases/1.2.3/HORUS_1.2.3_x64-setup.exe');
  assert.equal(result.release.size, 9);
  assert.match(result.release.sha256, /^[a-f0-9]{64}$/);
  for (const change of [{ installer: Buffer.alloc(0) }, { signature: '' }, { commit: 'bad' }, { pub_date: '' }, { notes: '' }]) assert.throws(() => candidate({ ...args, ...change }));
});

function fixture() {
  const installer = Buffer.from('synthetic');
  const signature = 'synthetic-signature';
  const bundle = candidate({ tag: 'v1.2.3', installer, signature, origin: 'https://updates.example', notes: 'Notas', pub_date: '2026-11-17T00:00:00Z', commit: 'a'.repeat(40) });
  const objects = new Map(), writes = [];
  const storage = { async get(key) { return objects.get(key) ?? null; }, async put(key, bytes) { writes.push(key); objects.set(key, Buffer.from(bytes)); } };
  return { installer, signature, bundle, objects, writes, storage, validate: async () => {}, verifySignature: async () => {} };
}
test('falha de assinatura não escreve nenhum objeto', async () => {
  const f = fixture();
  await assert.rejects(publish({ ...f, verifySignature: () => { throw Error('assinatura inválida'); } }), /assinatura/);
  assert.equal(f.writes.length, 0);
});
test('objeto órfão já existente recusa duplicata sem sobrescrever', async () => {
  const f = fixture();
  f.objects.set('production/releases/1.2.3/HORUS_1.2.3_x64-setup.exe.sig', Buffer.from('old'));
  await assert.rejects(publish(f), /duplicada/);
  assert.equal(f.writes.length, 0);
});
test('artefato perdido no storage bloqueia latest antes do validador', async () => {
  const f = fixture();
  let validated = false;
  f.storage.put = async (key, bytes) => { if (!key.endsWith('.sig')) f.objects.set(key, Buffer.from(bytes)); };
  await assert.rejects(publish({ ...f, validate: () => { validated = true; } }), /ausente/);
  assert.equal(validated, false);
  assert.equal(f.objects.has('production/latest.json'), false);
});
test('mudança concorrente de latest bloqueia PUT e preserva manifesto novo', async () => {
  const f = fixture();
  const previous = JSON.stringify({ version: '1.0.0' });
  f.objects.set('production/latest.json', Buffer.from(previous));
  await assert.rejects(publish({ ...f, validate: () => { f.objects.set('production/latest.json', Buffer.from(JSON.stringify({ version: '1.1.0' }))); } }), /mudou/);
  assert.equal(f.writes.includes('production/latest.json'), false);
  assert.equal(JSON.parse(f.objects.get('production/latest.json')).version, '1.1.0');
});
test('latest usa PUT condicional único quando o storage oferece CAS', async () => {
  const f = fixture();
  let calls = 0;
  f.storage.putLatest = async (key, bytes) => { calls++; assert.equal(f.objects.size, 3); f.objects.set(key, Buffer.from(bytes)); };
  await publish(f);
  assert.equal(calls, 1);
  assert.equal(f.writes.includes('production/latest.json'), false);
});
