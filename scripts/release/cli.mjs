import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { releaseTag, guard, productVersions, baseURL, overlay, candidate, publish, installerName, sha256 } from './core.mjs';
import { root, run, tauri, signatureCheck, buildVerifier } from './runtime.mjs';

const env = process.env;
const dir = path.resolve(env.HORUS_RELEASE_DIR || path.join(os.tmpdir(), 'horus-release-candidate'));
const tag = env.GITHUB_REF_NAME;
const write = (name, data) => fs.writeFileSync(path.join(dir, name), typeof data === 'string' || Buffer.isBuffer(data) ? data : JSON.stringify(data, null, 2));
const read = name => fs.readFileSync(path.join(dir, name));
const bundle = () => JSON.parse(read('candidate.json'));
function configuration() {
  const parsed = releaseTag(tag);
  if (env.HORUS_UPDATE_CHANNEL !== parsed.channel) throw Error('Canal do ambiente diverge da tag.');
  const origin = baseURL(env.HORUS_UPDATE_BASE_URL);
  if (!env.HORUS_CI_READ_TOKEN) throw Error('Token de leitura de CI ausente.');
  return { ...parsed, origin };
}
async function workerGet(key, missing = false) {
  const { origin } = configuration();
  const response = await fetch(`${origin}/${key}`, { headers: { Authorization: `Bearer ${env.HORUS_CI_READ_TOKEN}` }, redirect: 'error', signal: AbortSignal.timeout(120000) });
  if (missing && response.status === 404) return null;
  if (!response.ok) throw Error(`Worker recusou leitura (${response.status}); não interpretar falha de autenticação como canal vazio.`);
  return Buffer.from(await response.arrayBuffer());
}
async function checkRemote() {
  const { channel, version } = configuration();
  guard({ tag, versions: productVersions(root) });
  const latest = await workerGet(`${channel}/latest.json`, true);
  const existing = await Promise.all(['release.json', installerName(version), `${installerName(version)}.sig`].map(async name => {
    const { origin } = configuration();
    const response = await fetch(`${origin}/${channel}/releases/${version}/${name}`, { headers: { Authorization: `Bearer ${env.HORUS_CI_READ_TOKEN}` }, redirect: 'error', signal: AbortSignal.timeout(120000) });
    await response.body?.cancel();
    if (response.status === 404) return false;
    if (!response.ok) throw Error('Verificação de duplicata recusada pelo Worker.');
    return true;
  }));
  return guard({ tag, versions: productVersions(root), latest: latest ? JSON.parse(latest).version : undefined, exists: existing.some(Boolean) });
}
function r2Storage() {
  const { channel } = configuration();
  if (!env.HORUS_R2_BUCKET || !/^[a-z0-9][a-z0-9.-]+$/.test(env.HORUS_R2_BUCKET)) throw Error('Bucket dedicado ausente/inválido.');
  const endpoint = new URL(env.HORUS_R2_ENDPOINT);
  if (endpoint.protocol !== 'https:' || !/^[a-f0-9]+\.r2\.cloudflarestorage\.com$/.test(endpoint.hostname) || endpoint.pathname !== '/' || endpoint.search || endpoint.hash || endpoint.username || endpoint.password) throw Error('Endpoint R2 inválido.');
  const args = ['--endpoint-url', endpoint.origin, 's3api'];
  const etags = new Map();
  const scoped = key => { if (!key.startsWith(`${channel}/`)) throw Error('Objeto fora do canal.'); return ['--bucket', env.HORUS_R2_BUCKET, '--key', key]; };
  return {
    // AWS get-object usa arquivo temporário; erros desconhecidos nunca são tratados como ausência.
    async get(key) {
      const tmp = path.join(dir, 'r2-read.tmp');
      try {
        const { spawnSync } = await import('node:child_process');
        const result = spawnSync('aws', [...args, 'get-object', ...scoped(key), tmp], { encoding: 'utf8', env: { ...env, AWS_PAGER: '' } });
        if (result.status !== 0) {
          if (/\(NoSuchKey\)/.test(result.stderr || '')) { etags.set(key, null); return null; }
          throw Error('Leitura R2 falhou; saída omitida.');
        }
        const etag = JSON.parse(result.stdout).ETag;
        if (!etag) throw Error('Objeto R2 sem ETag.');
        etags.set(key, etag);
        return fs.readFileSync(tmp);
      } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
    },
    async put(key, data, immutable) {
      const tmp = path.join(dir, 'r2-write.tmp');
      try {
        fs.writeFileSync(tmp, data);
        run('aws', [...args, 'put-object', ...scoped(key), '--body', tmp, '--content-type', key.endsWith('.json') ? 'application/json' : 'application/octet-stream', ...(immutable ? ['--if-none-match', '*'] : [])]);
      } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
    },
    async response(key) {
      const bytes = await this.get(key);
      return bytes === null ? new Response(null, { status: 404 }) : new Response(bytes, { headers: { ETag: etags.get(key) } });
    },
    async putLatest(key, data) {
      if (!etags.has(key)) throw Error('ETag de latest não consultado.');
      const tmp = path.join(dir, 'r2-latest.tmp');
      try {
        fs.writeFileSync(tmp, data);
        const etag = etags.get(key);
        run('aws', [...args, 'put-object', ...scoped(key), '--body', tmp, '--content-type', 'application/json', '--cache-control', 'no-store', ...(etag ? ['--if-match', etag] : ['--if-none-match', '*'])]);
      } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
    },
  };
}
async function main() {
  fs.mkdirSync(dir, { recursive: true });
  switch (process.argv[2]) {
    case 'verify-version': {
      const result = await checkRemote();
      if (env.GITHUB_OUTPUT) fs.appendFileSync(env.GITHUB_OUTPUT, `channel=${result.channel}\nversion=${result.version}\n`);
      console.log(`Versão validada: ${result.version} (${result.channel}).`);
      break;
    }
    case 'overlay': {
      const result = overlay(env, JSON.parse(fs.readFileSync(path.join(root, 'frontend/src-tauri/tauri.release.conf.json'))));
      write('tauri.generated.json', result);
      break;
    }
    case 'collect': {
      const { version } = releaseTag(tag);
      const name = installerName(version);
      const source = path.join(root, 'frontend/src-tauri/target/release/bundle/nsis', name);
      write(name, fs.readFileSync(source));
      // A chave é exigida pelo Tauri para createUpdaterArtifacts; o job sign assina/verifica de novo.
      write(`${name}.sig`, fs.readFileSync(`${source}.sig`));
      break;
    }
    case 'sign': {
      const { version } = releaseTag(tag);
      if (!env.TAURI_SIGNING_PRIVATE_KEY || !env.HORUS_UPDATER_PUBKEY) throw Error('Chaves do ambiente ausentes.');
      run(process.execPath, [tauri, 'signer', 'sign', '--app-version', version, path.join(dir, installerName(version))]);
      buildVerifier();
      signatureCheck(env.HORUS_UPDATER_PUBKEY, read(installerName(version)), read(`${installerName(version)}.sig`), version);
      break;
    }
    case 'candidate': {
      const { version } = configuration();
      if (!env.HORUS_RELEASE_NOTES?.trim()) throw Error('Notas obrigatórias em HORUS_RELEASE_NOTES.');
      const result = candidate({ tag, installer: read(installerName(version)), signature: read(`${installerName(version)}.sig`).toString(), origin: env.HORUS_UPDATE_BASE_URL, notes: env.HORUS_RELEASE_NOTES, pub_date: new Date().toISOString(), commit: env.GITHUB_SHA });
      write('candidate.json', result); write('release.json', result.release); write('latest.candidate.json', result.latest);
      break;
    }
    case 'publish': {
      await checkRemote();
      const result = bundle();
      const { channel, version, origin } = configuration();
      if (result.version !== version || result.channel !== channel || result.latest.platforms['windows-x86_64'].url !== `${origin}/${channel}/releases/${version}/${installerName(version)}`) throw Error('Candidato diverge da tag/origem.');
      const regenerated = candidate({ tag, installer: read(installerName(version)), signature: read(`${installerName(version)}.sig`).toString(), origin, notes: result.release.notes, pub_date: result.release.pub_date, commit: env.GITHUB_SHA });
      if (JSON.stringify(result) !== JSON.stringify(regenerated)) throw Error('Candidato diverge dos artefatos, metadados ou commit.');
      // Contrato de integração explícito; ausência nunca aprova a publicação.
      const validator = path.join(root, 'infra/cloudflare/validate-release.mjs');
      if (!fs.existsSync(validator)) throw Error('Bloqueado: integrar infra/cloudflare/validate-release.mjs (contrato no README).');
      if (channel === 'production') {
        const protection = JSON.parse(run('gh', ['api', `repos/${env.GITHUB_REPOSITORY}/environments/production`]));
        if (!protection.protection_rules?.some(r => r.type === 'required_reviewers' && r.reviewers?.length > 0)) throw Error('Environment production sem revisor obrigatório; publicação recusada.');
      }
      buildVerifier();
      const { validateRelease } = await import(pathToFileURL(validator).href);
      if (typeof validateRelease !== 'function') throw Error('Contrato de validateRelease indisponível.');
      const storage = r2Storage();
      await publish({ storage, bundle: result, installer: read(result.filename), signature: read(`${result.filename}.sig`).toString(), verifySignature: (data, sig, ver) => signatureCheck(env.HORUS_UPDATER_PUBKEY, data, sig, ver), validate: () => validateRelease({ store: { get: key => storage.response(key) }, channel, version, candidate: result.latest, pubkey: env.HORUS_UPDATER_PUBKEY, baseUrl: origin, tag }) });
      break;
    }
    case 'post-verify': {
      const expected = bundle();
      const latest = JSON.parse(await workerGet(`${expected.channel}/latest.json`));
      if (JSON.stringify(latest) !== JSON.stringify(expected.latest)) throw Error('Manifesto publicado diverge do candidato.');
      const prefix = `${expected.channel}/releases/${expected.version}`;
      const installer = await workerGet(`${prefix}/${expected.filename}`);
      const sig = await workerGet(`${prefix}/${expected.filename}.sig`);
      const metadata = JSON.parse(await workerGet(`${prefix}/release.json`));
      if (sha256(installer) !== expected.release.sha256 || installer.length !== expected.release.size || JSON.stringify(metadata) !== JSON.stringify(expected.release) || sig.toString().trim() !== latest.platforms['windows-x86_64'].signature) throw Error('Integridade pós-publicação inválida.');
      buildVerifier(); signatureCheck(env.HORUS_UPDATER_PUBKEY, installer, sig, expected.version);
      console.log('Pós-publicação: hash, tamanho, metadados e assinatura válidos via Worker autenticado.');
      break;
    }
    default: throw Error('Comando de release desconhecido.');
  }
}
main().catch(() => { console.error('Release recusada; verifique configuração, versões, artefatos e contrato do validador (detalhes omitidos para proteger segredos).'); process.exitCode = 1; });
