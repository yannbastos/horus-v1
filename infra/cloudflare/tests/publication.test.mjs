import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { Miniflare } from 'miniflare';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { validateRelease } from '../validate-release.mjs';
import { publishManifest } from '../publish-manifest.mjs';
import { issue, revoke, list } from '../admin/tokens.mjs';
import { createStore } from '../storage.mjs';

// Fixture PÚBLICA CC0-1.0, Kaito Udagawa (2026), minisign-js/test_fixtures/test.dat.
// https://github.com/kaito-tokyo/minisign-js/tree/main/@kaito-tokyo/minisign-verify/test_fixtures
// Nenhuma chave privada copiada/gerada. Esta chave pública nunca deve entrar em builds.
const data = Buffer.from('test\n');
const sig = `untrusted comment: signature from minisign secret key
RUSBf6Tu0QagxTohs9/kgLitMaShsRf5NEjOn9/aO03tpklkYTBR250i64LYccpTqx4Y02iJHmFCJ5FqmfbP1QlnlxYvdcmeTgM=
trusted comment: timestamp:1774593131\tfile:test.dat\thashed
0vQ/suz1GmbJHBoQMKOqYsTQ6Yi+baQj2Km6d222R2x1AQYGcW6UBf5iRU16fWr43R6+DTqwk5u5JKXTJ/TSBA==
`;
const pub = `untrusted comment: minisign public key C5A006D1EEA47F81
RWSBf6Tu0QagxVJavce10/fhuZs6PVLtjH/OfT0ma6nrwbyzTaqFXB9s
`;
const encodedSig = Buffer.from(sig).toString('base64');
const pubkey = Buffer.from(pub).toString('base64');
const version = '1.2.3-test.2';
const prefix = `test/releases/${version}/`;
const key = prefix + `HORUS_${version}_x64-setup.exe`;
const candidate = { version, notes: 'Ensaio', pub_date: '2026-11-17T00:00:00Z',
  platforms: { 'windows-x86_64': { signature: encodedSig, url: `https://updates.example/${key}` } } };
const release = { version, sha256: createHash('sha256').update(data).digest('hex'), size: data.length,
  notes: candidate.notes, pub_date: candidate.pub_date, commit: 'a'.repeat(40) };
let mf, bucket, store, options, writes;
beforeAll(async () => {
  mf = new Miniflare({ modules: true, script: 'export default {fetch(){return new Response("local")}}',
    compatibilityDate: '2026-07-30', r2Buckets: ['RELEASES'] });
  bucket = await mf.getR2Bucket('RELEASES');
});
afterAll(async () => { await mf?.dispose(); });
beforeEach(async () => {
  await bucket.delete('test/latest.json');
  await bucket.put(key, data); await bucket.put(key + '.sig', encodedSig);
  await bucket.put(prefix + 'release.json', JSON.stringify(release));
  writes = [];
  store = {
    async get(name) {
      const object = await bucket.get(name);
      return object ? new Response(await object.arrayBuffer(), { headers: { ETag: object.httpEtag } }) : new Response(null, { status: 404 });
    },
    async putLatest(name, body, etag) {
      const result = await bucket.put(name, body, { onlyIf: etag ? { etagMatches: etag.replace(/^"|"$/g, '') } : { etagDoesNotMatch: '*' } });
      if (!result) throw new Error('Publicação concorrente recusada');
      writes.push(name);
    },
  };
  options = { store, channel: 'test', version, candidate: structuredClone(candidate), pubkey, baseUrl: 'https://updates.example', tag: `v${version}` };
});

describe('Validação e publicação atômica (R2 Miniflare)', () => {
  it('verifica minisign real, SHA-256 e tamanho sem publicar', async () => {
    const result = await validateRelease(options);
    expect(result.release).toEqual(release); expect(writes).toEqual([]);
    expect(await bucket.head('test/latest.json')).toBeNull();
  });
  it('publica apenas um PUT após validar os três artefatos', async () => {
    expect(await publishManifest(options)).toBe(version);
    expect(writes).toEqual(['test/latest.json']);
    expect(await (await bucket.get('test/latest.json')).json()).toEqual(candidate);
  });
  it.each([key, key + '.sig', prefix + 'release.json'])('não publica com artefato ausente: %s', async missing => {
    await bucket.delete(missing);
    await expect(publishManifest(options)).rejects.toThrow();
    expect(writes).toEqual([]); expect(await bucket.head('test/latest.json')).toBeNull();
  });
  it.each([{ size: data.length + 1 }, { sha256: 'b'.repeat(64) }, { version: '9.9.9' }, { commit: 'inválido' }, { notes: 'outras' }])('recusa release.json divergente %j', async patch => {
    await bucket.put(prefix + 'release.json', JSON.stringify({ ...release, ...patch }));
    await expect(publishManifest(options)).rejects.toThrow(); expect(writes).toEqual([]);
  });
  it('recusa arquivo adulterado mesmo com SHA atualizado sem assinatura válida', async () => {
    const changed = Buffer.from('evil\n'); await bucket.put(key, changed);
    await bucket.put(prefix + 'release.json', JSON.stringify({ ...release, sha256: createHash('sha256').update(changed).digest('hex') }));
    await expect(publishManifest(options)).rejects.toThrow('Assinatura minisign inválida'); expect(writes).toEqual([]);
  });
  it('recusa chave diferente', async () => {
    const changed = Buffer.from(pub.split('\n')[1], 'base64'); changed[41] ^= 1;
    options.pubkey = Buffer.from(`untrusted comment: test\n${changed.toString('base64')}\n`).toString('base64');
    await expect(publishManifest(options)).rejects.toThrow(); expect(writes).toEqual([]);
  });
  it('recusa assinatura no manifesto diferente do .sig', async () => {
    options.candidate.platforms['windows-x86_64'].signature = 'wrong';
    await expect(publishManifest(options)).rejects.toThrow('Assinatura divergente');
  });
  it.each(['https://evil.example/x.exe', `https://updates.example/production/releases/${version}/setup.exe`, `http://updates.example/${key}`])('recusa URL fora do contrato %s', async url => {
    options.candidate.platforms['windows-x86_64'].url = url;
    await expect(publishManifest(options)).rejects.toThrow('URL');
  });
  it.each(['test', 'production'])('isola validação do canal %s', async channel => {
    options.channel = channel === 'test' ? 'production' : 'test';
    options.version = channel === 'test' ? version : '1.2.3';
    await expect(publishManifest(options)).rejects.toThrow('canal');
  });
  it.each([version, '1.2.3-test.3', '2.0.0-test.1'])('recusa duplicação/regressão contra %s', async previous => {
    await bucket.put('test/latest.json', JSON.stringify({ ...candidate, version: previous }));
    await expect(publishManifest(options)).rejects.toThrow('duplicada ou regressiva'); expect(writes).toEqual([]);
  });
  it('recusa tag diferente sem alterar versão', async () => {
    options.tag = 'v9.9.9'; await expect(publishManifest(options)).rejects.toThrow('Tag divergente');
  });
  it('CAS impede sobrescrever publicação concorrente', async () => {
    const put = store.putLatest;
    store.putLatest = async (...args) => {
      await bucket.put('test/latest.json', JSON.stringify({ ...candidate, version: '3.0.0-test.1' }));
      return put(...args);
    };
    await expect(publishManifest(options)).rejects.toThrow('concorrente');
    expect((await (await bucket.get('test/latest.json')).json()).version).toBe('3.0.0-test.1');
  });
  it('atualiza manifesto anterior com ETag condicional', async () => {
    await bucket.put('test/latest.json', JSON.stringify({ ...candidate, version: '1.2.3-test.1' }));
    expect(await publishManifest(options)).toBe(version); expect(writes).toHaveLength(1);
  });
  it('simulador de diretório valida e escreve arquivo atomicamente', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'horus-publication-test-'));
    try {
      await mkdir(join(directory, prefix), { recursive: true });
      await writeFile(join(directory, key), data); await writeFile(join(directory, key + '.sig'), encodedSig);
      await writeFile(join(directory, prefix, 'release.json'), JSON.stringify(release));
      const local = createStore('test', directory);
      await publishManifest({ ...options, store: local });
      expect(JSON.parse(await readFile(join(directory, 'test/latest.json'), 'utf8'))).toEqual(candidate);
      await expect(local.putLatest('test/latest.json', '{}', null)).rejects.toThrow('concorrente');
    } finally { await rm(directory, { recursive: true }); }
  });
});

describe('Administração sem persistir token', () => {
  it('CLI recusa emissão com stdout redirecionado antes de gerar token', async () => {
    try {
      await promisify(execFile)(process.execPath, ['admin/tokens.mjs', 'issue', 'test', 'Nunca emitir em logs'], { cwd: new URL('..', import.meta.url) });
      throw new Error('Deveria recusar');
    } catch (error) {
      expect(error.code).toBe(1); expect(error.stdout).toBe('');
      expect(error.stderr).toContain('terminal interativo');
    }
  });
  it('emite 256 bits, persiste só hash, lista sem segredos', async () => {
    const records = new Map();
    const admin = { put: async (id, record) => records.set(id, record), get: async id => records.get(id), list: async () => [...records.keys()] };
    const { token, id } = await issue(admin, 'test', 'Estação');
    expect(Buffer.from(token, 'base64url')).toHaveLength(32);
    expect(id).toBe(createHash('sha256').update(token).digest('hex'));
    expect(JSON.stringify([...records])).not.toContain(token);
    expect(await list(admin, 'test')).toEqual([{ id, ...records.get(id) }]);
    expect(JSON.stringify(await list(admin, 'test'))).not.toContain(token);
    expect(await list(admin, 'production')).toEqual([]);
  });
  it('grava marcador R2 antes de ler/gravar flag KV', async () => {
    const calls = [];
    await revoke({ revokeMarker: async () => calls.push('r2'), get: async () => { calls.push('kv-get'); return { channel: 'test', revoked: false }; }, put: async (_, value) => { calls.push('kv-put'); expect(value.revoked).toBe(true); } }, 'test', 'a'.repeat(64));
    expect(calls).toEqual(['r2', 'kv-get', 'kv-put']);
  });
  it('falha R2 interrompe revogação, falha KV reporta revogação parcial segura', async () => {
    let touched = false;
    await expect(revoke({ revokeMarker: async () => { throw new Error('R2 falhou'); }, get: async () => { touched = true; } }, 'test', 'a'.repeat(64))).rejects.toThrow('R2 falhou');
    expect(touched).toBe(false);
    await expect(revoke({ revokeMarker: async () => {}, get: async () => { throw new Error('KV falhou'); } }, 'test', 'a'.repeat(64))).rejects.toThrow('Acesso revogado no R2');
  });
  it('recusa entrada inválida antes de manipular storage', async () => {
    await expect(issue({}, 'test', '\n')).rejects.toThrow('Rótulo');
    await expect(issue({}, 'other', 'x')).rejects.toThrow('Canal');
    await expect(revoke({}, 'test', 'token')).rejects.toThrow('ID');
  });
});
