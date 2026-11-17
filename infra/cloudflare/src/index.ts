export interface Env {
  CHANNEL: 'test' | 'production';
  RELEASES: R2Bucket;
  INSTALLATIONS: KVNamespace;
}

interface Installation { label: string; channel: string; created: string; revoked: boolean }
const versionPattern = '(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)(?:-test\\.(?:0|[1-9]\\d*))?';
const versionRE = new RegExp(`^${versionPattern}$`);
const releaseRE = new RegExp(`^releases/(${versionPattern})/(.+)$`);
const common = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Vary': 'Authorization' };
const deny = () => new Response('Acesso negado', { status: 403, headers: common });
const unavailable = () => new Response('Indisponível', { status: 503, headers: common });
const notFound = () => new Response('Não encontrado', { status: 404, headers: common });

// Proteção local, limitada e sem persistir IP/token. Não representa cota global.
const attempts = new Map<string, { count: number; until: number }>();
function limited(request: Request): boolean {
  const ip = request.headers.get('CF-Connecting-IP');
  if (!ip) return false;
  const now = Date.now();
  let item = attempts.get(ip);
  if (item && item.until <= now) { attempts.delete(ip); item = undefined; }
  if (!item) {
    if (attempts.size >= 2048) {
      for (const [key, value] of attempts) if (value.until <= now) attempts.delete(key);
      if (attempts.size >= 2048) return true;
    }
    item = { count: 0, until: now + 60_000 };
    attempts.set(ip, item);
  }
  return ++item.count > 120;
}

async function authorized(request: Request, env: Env): Promise<boolean> {
  const match = /^Bearer ([A-Za-z0-9_-]{43})$/.exec(request.headers.get('Authorization') ?? '');
  if (!match) return false;
  const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(match[1])))]
    .map(value => value.toString(16).padStart(2, '0')).join('');
  // R2 é fortemente consistente; não usar Cache API nem cachear permissões KV.
  if (await env.RELEASES.head(`${env.CHANNEL}/revoked/${hash}`)) return false;
  const record = await env.INSTALLATIONS.get<Installation>(`installation:${hash}`, 'json');
  return !!record && record.channel === env.CHANNEL && record.revoked === false
    && typeof record.label === 'string' && typeof record.created === 'string';
}

function validVersion(channel: string, version: unknown): version is string {
  return typeof version === 'string' && versionRE.test(version)
    && (channel === 'test' ? version.includes('-test.') : !version.includes('-'));
}

async function manifest(request: Request, env: Env): Promise<Response> {
  const object = await env.RELEASES.get(`${env.CHANNEL}/latest.json`);
  if (!object) return notFound();
  if (object.size > 64 * 1024) return unavailable();
  const text = await object.text();
  let value;
  try { value = JSON.parse(text); } catch { return unavailable(); }
  if (!validVersion(env.CHANNEL, value.version) || typeof value.notes !== 'string'
      || typeof value.pub_date !== 'string' || !Number.isFinite(Date.parse(value.pub_date))) return unavailable();
  const platform = value.platforms?.['windows-x86_64'];
  const file = `HORUS_${value.version}_x64-setup.exe`;
  const key = `${env.CHANNEL}/releases/${value.version}/${file}`;
  // Nunca redirecionar Authorization para outro host, canal, query ou versão.
  const expected = new URL(`/${key}`, request.url);
  if (!platform || platform.url !== expected.href || typeof platform.signature !== 'string' || !platform.signature) return unavailable();
  const exists = await Promise.all([key, `${key}.sig`, `${env.CHANNEL}/releases/${value.version}/release.json`]
    .map(name => env.RELEASES.head(name)));
  if (exists.some(entry => !entry || entry.size === 0)) return unavailable();
  return new Response(request.method === 'HEAD' ? null : text, {
    headers: { ...common, 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function parseRange(value: string, size: number): { offset: number; length: number } | null {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2]) || size === 0) return null;
  let start: number;
  let end: number;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return null;
    start = Math.max(0, size - suffix); end = size - 1;
  } else {
    start = Number(match[1]); end = match[2] ? Number(match[2]) : size - 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= size || end < start) return null;
    end = Math.min(end, size - 1);
  }
  return { offset: start, length: end - start + 1 };
}

async function artifact(request: Request, env: Env, path: string): Promise<Response> {
  const match = releaseRE.exec(path);
  if (!match || !validVersion(env.CHANNEL, match[1])) return notFound();
  const file = `HORUS_${match[1]}_x64-setup.exe`;
  if (![file, `${file}.sig`, 'release.json'].includes(match[2])) return notFound();
  const key = `${env.CHANNEL}/${path}`;
  const metadata = await env.RELEASES.head(key);
  if (!metadata) return notFound();
  const headers = new Headers({ ...common,
    'Content-Type': match[2].endsWith('.exe') ? 'application/octet-stream'
      : match[2] === 'release.json' ? 'application/json; charset=utf-8' : 'text/plain; charset=utf-8',
    'Accept-Ranges': 'bytes', 'ETag': metadata.httpEtag,
    'Content-Length': String(metadata.size),
  });
  // RFC 9110: HEAD ignora Range. If-Range incompatível devolve o objeto inteiro.
  const rangeHeader = request.method === 'GET' ? request.headers.get('Range') : null;
  const ifRange = request.headers.get('If-Range');
  const range = rangeHeader && (!ifRange || ifRange === metadata.httpEtag) ? parseRange(rangeHeader, metadata.size) : undefined;
  if (range === null) {
    headers.set('Content-Range', `bytes */${metadata.size}`);
    headers.delete('Content-Length');
    return new Response(null, { status: 416, headers });
  }
  if (request.method === 'HEAD') return new Response(null, { headers });
  // Condicional evita servir metadados/faixa de um objeto substituído durante o GET.
  const object = await env.RELEASES.get(key, { onlyIf: { etagMatches: metadata.etag }, ...(range ? { range } : {}) });
  if (!object || !('body' in object)) return unavailable();
  if (range) {
    headers.set('Content-Range', `bytes ${range.offset}-${range.offset + range.length - 1}/${metadata.size}`);
    headers.set('Content-Length', String(range.length));
  }
  return new Response(object.body, { status: range ? 206 : 200, headers });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    // A autenticação precede método, rota e existência do objeto.
    try {
      if (!['test', 'production'].includes(env.CHANNEL)) return deny();
      if (limited(request)) return new Response('Limite temporário', { status: 429, headers: { ...common, 'Retry-After': '60' } });
      if (!await authorized(request, env)) return deny();
      if (!url.pathname.startsWith(`/${env.CHANNEL}/`)) return deny();
      if (url.protocol !== 'https:' && !(env.CHANNEL === 'test' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname))) return deny();
      if (!['GET', 'HEAD'].includes(request.method)) return new Response(null, { status: 405, headers: { ...common, Allow: 'GET, HEAD' } });
      const path = url.pathname.slice(env.CHANNEL.length + 2);
      if (path === 'latest.json') return await manifest(request, env);
      return await artifact(request, env, path);
    } catch {
      // Inclui falhas KV/R2: nunca autorizar em erro nem retornar detalhes internos.
      return deny();
    }
  },
} satisfies ExportedHandler<Env>;
