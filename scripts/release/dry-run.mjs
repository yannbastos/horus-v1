// Ensaio totalmente local: não constrói/executa NSIS, não lê cofre nem usa Cloudflare.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { candidate, publish, sha256 } from './core.mjs';
import { run, tauri, buildVerifier, signatureCheck } from './runtime.mjs';

const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'horus-release-dry-run-'));
const objects = new Map();
const writes = [];
const token = randomBytes(32).toString('hex');
const server = createServer((req, res) => {
  if (req.headers.authorization !== `Bearer ${token}`) { res.writeHead(401).end(); return; }
  const data = objects.get(req.url.slice(1));
  if (!data) { res.writeHead(404).end(); return; }
  res.writeHead(200).end(data);
});
const storage = {
  async get(key) { return objects.get(key) ?? null; },
  async put(key, data, immutable) {
    if (immutable && objects.has(key)) throw Error('Objeto imutável já existe.');
    objects.set(key, Buffer.from(data)); writes.push(key);
  },
};
try {
  buildVerifier();
  // Chave efêmera de TESTE fora do repo; nada em LOCALAPPDATA/Horus operacional.
  const key = path.join(temp, 'test.key');
  run(process.execPath, [tauri, 'signer', 'generate', '--ci', '--write-keys', key]);
  const publicKey = fs.readFileSync(`${key}.pub`, 'utf8');
  const version = '1.2.0-test.1';
  const file = path.join(temp, 'synthetic.exe');
  fs.writeFileSync(file, 'HORUS synthetic release; NOT an executable\n');
  run(process.execPath, [tauri, 'signer', 'sign', '--app-version', version, file], { env: { ...process.env, TAURI_SIGNING_PRIVATE_KEY: fs.readFileSync(key, 'utf8'), TAURI_SIGNING_PRIVATE_KEY_PASSWORD: '' } });
  const installer = fs.readFileSync(file), signature = fs.readFileSync(`${file}.sig`, 'utf8');
  const verifySignature = (data, sig, ver) => signatureCheck(publicKey, data, sig, ver);
  verifySignature(installer, signature, version);
  assert.throws(() => verifySignature(Buffer.from('tampered'), signature, version));
  assert.throws(() => verifySignature(installer, signature, '1.2.0-test.2'));
  await new Promise(ok => server.listen(0, '127.0.0.1', ok));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const bundle = candidate({ tag: `v${version}`, installer, signature, origin: 'https://updates.invalid', notes: 'Ensaio sintético isolado', pub_date: '2026-11-17T00:00:00Z', commit: 'a'.repeat(40) });
  const validatorPath = process.argv[2];
  const validate = async value => {
    for (const name of [value.filename, `${value.filename}.sig`, 'release.json']) assert.ok(objects.has(`test/releases/${version}/${name}`));
    assert.equal(objects.has('test/latest.json'), false, 'Manifesto jamais precede os artefatos.');
    if (validatorPath) {
      const { validateRelease } = await import(pathToFileURL(path.resolve(validatorPath)).href);
      await validateRelease({ store: { async get(key) { const bytes = objects.get(key); return bytes ? new Response(bytes, { headers: { ETag: `"${sha256(bytes)}"` } }) : new Response(null, { status: 404 }); } }, channel: value.channel, version: value.version, candidate: value.latest, pubkey: publicKey, baseUrl: 'https://updates.invalid', tag: `v${version}` });
    }
  };
  await publish({ storage, bundle, installer, signature, verifySignature, validate });
  assert.equal(writes.at(-1), 'test/latest.json');
  assert.equal(writes.filter(k => k === 'test/latest.json').length, 1);
  const headers = { Authorization: `Bearer ${token}` };
  assert.equal((await fetch(`${origin}/test/latest.json`)).status, 401);
  const latest = await (await fetch(`${origin}/test/latest.json`, { headers })).json();
  const downloaded = Buffer.from(await (await fetch(`${origin}${new URL(latest.platforms['windows-x86_64'].url).pathname}`, { headers })).arrayBuffer());
  assert.equal(sha256(downloaded), bundle.release.sha256);
  verifySignature(downloaded, latest.platforms['windows-x86_64'].signature, version);
  const before = Buffer.from(objects.get('test/latest.json'));
  await assert.rejects(publish({ storage, bundle, installer, signature, verifySignature, validate }), /duplicada/);
  const lower = candidate({ tag: 'v1.1.0-test.1', installer, signature, origin: 'https://updates.invalid', notes: 'Regressiva', pub_date: '2026-11-17T00:00:00Z', commit: 'b'.repeat(40) });
  await assert.rejects(publish({ storage, bundle: lower, installer, signature, verifySignature, validate }), /regressiva/);
  assert.ok(objects.get('test/latest.json').equals(before));
  const emptyStorage = { async get() { return null; }, async put(key) { assert.fail(`PUT indevido: ${key}`); } };
  await assert.rejects(publish({ storage: emptyStorage, bundle, installer: null, signature, verifySignature, validate }), /ausente/);
  const failedObjects = new Map();
  const failStorage = { async get(key) { return failedObjects.get(key) ?? null; }, async put(key, data) { failedObjects.set(key, Buffer.from(data)); } };
  await assert.rejects(publish({ storage: failStorage, bundle, installer, signature, verifySignature, validate: () => { throw Error('Validador recusou'); } }), /recusou/);
  assert.equal(failedObjects.has('test/latest.json'), false);
  console.log(`PASS: publicação feliz, leitura autenticada/hash/minisign, adulteração e versão assinada, duplicata, regressão, artefato ausente, validador recusado e latest por último (stub local${validatorPath ? ' + validador A' : ''}).`);
} finally {
  server.closeAllConnections();
  await new Promise(ok => server.close(ok));
  fs.rmSync(temp, { recursive: true });
}
