import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { Miniflare } from 'miniflare';
import { transform } from 'esbuild';
import { readFile } from 'node:fs/promises';
import { createHash, randomBytes } from 'node:crypto';
import worker from '../src/index';

const token = randomBytes(32).toString('base64url');
const hash = createHash('sha256').update(token).digest('hex');
const version = '1.2.3-test.1';
const file = `HORUS_${version}_x64-setup.exe`;
const key = `test/releases/${version}/${file}`;
const origin = 'https://updates.example';
const record = { label: 'Ensaio', channel: 'test', created: '2026-11-17T00:00:00Z', revoked: false };
const candidate = { version, notes: 'Teste', pub_date: record.created,
  platforms: { 'windows-x86_64': { signature: 'dummy-public-signature', url: `${origin}/${key}` } } };
let mf: Miniflare;
let compiled: string;
let kv: Awaited<ReturnType<Miniflare['getKVNamespace']>>;
let r2: Awaited<ReturnType<Miniflare['getR2Bucket']>>;
async function request(path = '/test/latest.json', options: RequestInit = {}, bearer: string | null = token) {
  return mf.dispatchFetch(origin + path, { ...options, headers: { ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}), ...options.headers } });
}

beforeAll(async () => {
  const source = await readFile(new URL('../src/index.ts', import.meta.url), 'utf8');
  const { code } = await transform(source, { loader: 'ts', format: 'esm', target: 'es2022' });
  compiled = code;
  mf = new Miniflare({ modules: true, script: code, compatibilityDate: '2026-07-30',
    bindings: { CHANNEL: 'test' }, kvNamespaces: ['INSTALLATIONS'], r2Buckets: ['RELEASES'] });
  [kv, r2] = await Promise.all([mf.getKVNamespace('INSTALLATIONS'), mf.getR2Bucket('RELEASES')]);
}, 30_000);
afterAll(async () => { await mf?.dispose(); });
beforeEach(async () => {
  await kv.put(`installation:${hash}`, JSON.stringify(record));
  await r2.delete(`test/revoked/${hash}`);
  await r2.put('test/latest.json', JSON.stringify(candidate));
  await r2.put(key, '0123456789');
  await r2.put(key + '.sig', 'signature');
  await r2.put(`test/releases/${version}/release.json`, '{}');
});

describe('Worker com R2/KV Miniflare locais', () => {
  it.each([null, 'errado', randomBytes(32).toString('base64url')])('nega credencial ausente/inválida sem revelar existência', async bearer => {
    const results = await Promise.all(['/test/latest.json', '/test/releases/9.9.9/inexistente.exe', '/production/latest.json']
      .map(path => request(path, {}, bearer)));
    for (const response of results) {
      expect(response.status).toBe(403);
      expect(await response.text()).toBe('Acesso negado');
      expect(response.headers.get('cache-control')).toBe('no-store');
    }
  });
  it.each([{ ...record, revoked: true }, { ...record, channel: 'production' }])('nega token revogado ou de outro canal', async value => {
    await kv.put(`installation:${hash}`, JSON.stringify(value));
    expect((await request()).status).toBe(403);
  });
  it('serve manifesto Tauri sem cache e HEAD', async () => {
    const response = await request();
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toContain('application/json');
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(await response.json()).toEqual(candidate);
    const head = await request(undefined, { method: 'HEAD' });
    expect(head.status).toBe(200); expect(await head.text()).toBe('');
  });
  it('transmite instalador completo e HEAD ignora Range', async () => {
    const response = await request('/' + key);
    expect(response.status).toBe(200); expect(await response.text()).toBe('0123456789');
    const head = await request('/' + key, { method: 'HEAD', headers: { Range: 'bytes=2-4' } });
    expect(head.status).toBe(200); expect(head.headers.get('content-length')).toBe('10'); expect(await head.text()).toBe('');
  });
  it.each([['bytes=2-4', '234', 'bytes 2-4/10'], ['bytes=7-', '789', 'bytes 7-9/10'], ['bytes=-3', '789', 'bytes 7-9/10'], ['bytes=8-99', '89', 'bytes 8-9/10']])('serve Range %s', async (range, body, contentRange) => {
    const response = await request('/' + key, { headers: { Range: range } });
    expect(response.status).toBe(206); expect(await response.text()).toBe(body);
    expect(response.headers.get('content-range')).toBe(contentRange);
    expect(response.headers.get('content-length')).toBe(String(body.length));
  });
  it.each(['bytes=99-', 'bytes=4-2', 'bytes=0-1,4-5', 'bytes=-0', 'invalid'])('recusa Range inválido %s', async range => {
    const response = await request('/' + key, { headers: { Range: range } });
    expect(response.status).toBe(416); expect(response.headers.get('content-range')).toBe('bytes */10');
  });
  it('If-Range divergente serve objeto completo', async () => {
    const response = await request('/' + key, { headers: { Range: 'bytes=1-2', 'If-Range': '"antigo"' } });
    expect(response.status).toBe(200); expect(await response.text()).toBe('0123456789');
  });
  it('revogação é imediata mesmo com flag KV ativa', async () => {
    expect((await request()).status).toBe(200);
    await r2.put(`test/revoked/${hash}`, '{}');
    expect(JSON.parse((await kv.get(`installation:${hash}`))!).revoked).toBe(false);
    for (const path of ['/test/latest.json', '/' + key]) expect((await request(path)).status).toBe(403);
  });
  it.each([key, key + '.sig', `test/releases/${version}/release.json`])('latest nunca aponta para artefato ausente: %s', async missing => {
    await r2.delete(missing);
    const response = await request();
    expect(response.status).toBe(503); expect(await response.text()).toBe('Indisponível');
  });
  it('isola canal mesmo se houver objeto e cadastro no bucket errado', async () => {
    await r2.put('production/latest.json', JSON.stringify(candidate));
    expect((await request('/production/latest.json')).status).toBe(403);
  });
  it('ambiente production tem bucket/KV próprios e aceita apenas seu token', async () => {
    const production = new Miniflare({ modules: true, script: compiled, compatibilityDate: '2026-07-30',
      bindings: { CHANNEL: 'production' }, kvNamespaces: ['INSTALLATIONS'], r2Buckets: ['RELEASES'] });
    try {
      const prodKV = await production.getKVNamespace('INSTALLATIONS');
      const prodR2 = await production.getR2Bucket('RELEASES');
      const prodToken = randomBytes(32).toString('base64url');
      const prodHash = createHash('sha256').update(prodToken).digest('hex');
      await prodKV.put(`installation:${prodHash}`, JSON.stringify({ ...record, channel: 'production' }));
      const prodKey = 'production/releases/1.2.3/HORUS_1.2.3_x64-setup.exe';
      await prodR2.put(prodKey, 'production'); await prodR2.put(prodKey + '.sig', 'sig');
      await prodR2.put('production/releases/1.2.3/release.json', '{}');
      await prodR2.put('production/latest.json', JSON.stringify({ ...candidate, version: '1.2.3',
        platforms: { 'windows-x86_64': { signature: 'sig', url: `${origin}/${prodKey}` } } }));
      const good = await production.dispatchFetch(`${origin}/production/latest.json`, { headers: { Authorization: `Bearer ${prodToken}` } });
      expect(good.status).toBe(200); expect((await good.json() as any).version).toBe('1.2.3');
      expect((await production.dispatchFetch(`${origin}/production/latest.json`, { headers: { Authorization: `Bearer ${token}` } })).status).toBe(403);
      expect((await request(undefined, {}, prodToken)).status).toBe(403);
      expect((await production.dispatchFetch('http://localhost/production/latest.json', { headers: { Authorization: `Bearer ${prodToken}` } })).status).toBe(403);
    } finally { await production.dispose(); }
  });
  it('HTTP só é permitido em loopback no ambiente test', async () => {
    expect((await mf.dispatchFetch('http://updates.example/test/latest.json', { headers: { Authorization: `Bearer ${token}` } })).status).toBe(403);
    const local = structuredClone(candidate);
    local.platforms['windows-x86_64'].url = `http://localhost/${key}`;
    await r2.put('test/latest.json', JSON.stringify(local));
    expect((await mf.dispatchFetch('http://localhost/test/latest.json', { headers: { Authorization: `Bearer ${token}` } })).status).toBe(200);
  });
  it('recusa URL de outra origem/canal e manifesto malformado', async () => {
    for (const url of ['https://evil.example/test/a.exe', `${origin}/production/releases/1.2.3/a.exe`]) {
      await r2.put('test/latest.json', JSON.stringify({ ...candidate, platforms: { 'windows-x86_64': { signature: 'x', url } } }));
      expect((await request()).status).toBe(503);
    }
    await r2.put('test/latest.json', '{'); expect((await request()).status).toBe(503);
  });
  it('sem resposta expor token, hash ou metadados da instalação', async () => {
    for (const path of ['/test/latest.json', '/' + key, '/test/invalid', '/production/latest.json']) {
      const response = await request(path);
      const text = JSON.stringify([...response.headers]) + await response.text();
      expect(text).not.toContain(token); expect(text).not.toContain(hash); expect(text).not.toContain(record.label);
    }
  });
  it('métodos de escrita não são expostos', async () => {
    expect((await request(undefined, { method: 'PUT', body: 'x' })).status).toBe(405);
  });
  it('nega em erro de R2, antes de consultar KV', async () => {
    let kvRead = false;
    const response = await worker.fetch(new Request(`${origin}/test/latest.json`, { headers: { Authorization: `Bearer ${token}` } }), {
      CHANNEL: 'test', RELEASES: { head() { throw new Error('indisponível'); } },
      INSTALLATIONS: { get() { kvRead = true; return record; } },
    } as any);
    expect(response.status).toBe(403); expect(kvRead).toBe(false); expect(await response.text()).toBe('Acesso negado');
  });
  it('nega em erro de KV e configuração ausente', async () => {
    const response = await worker.fetch(new Request(`${origin}/test/latest.json`, { headers: { Authorization: `Bearer ${token}` } }), {
      CHANNEL: 'test', RELEASES: { head: async () => null }, INSTALLATIONS: { get() { throw new Error('segredo interno'); } },
    } as any);
    expect(response.status).toBe(403); expect(await response.text()).toBe('Acesso negado');
    expect((await worker.fetch(new Request(origin), {} as any)).status).toBe(403);
  });
  it('limita abuso por IP sem consultar storage após o limite', async () => {
    const ip = '192.0.2.123';
    for (let i = 0; i < 120; i++) expect((await worker.fetch(new Request(origin, { headers: { 'CF-Connecting-IP': ip } }), { CHANNEL: 'test' } as any)).status).toBe(403);
    const response = await worker.fetch(new Request(origin, { headers: { 'CF-Connecting-IP': ip } }), { CHANNEL: 'test' } as any);
    expect(response.status).toBe(429); expect(response.headers.get('retry-after')).toBe('60');
  });
});
