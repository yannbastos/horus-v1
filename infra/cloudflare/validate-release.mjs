import { createHash } from 'node:crypto';
import { mkdtemp, readFile, open, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import semver from 'semver';
import { MinisignVerifier } from '@kaito-tokyo/minisign-verify';
import { argumentsForRelease, createStore } from './storage.mjs';

function assert(value, message) { if (!value) throw new Error(message); }
async function json(response, name) {
  assert(response.ok, `${name} ausente`);
  const text = await response.text();
  assert(text.length <= 64 * 1024, `${name} excede limite`);
  try { return JSON.parse(text); } catch { throw new Error(`${name} inválido`); }
}
function minisignText(encoded, name) {
  assert(typeof encoded === 'string' && /^[A-Za-z0-9+/]+={0,2}$/.test(encoded.trim()), `${name} deve ser base64 Tauri`);
  const text = Buffer.from(encoded.trim(), 'base64').toString('utf8');
  assert(text.startsWith('untrusted comment:'), `${name} inválida`);
  return text;
}
function publicKey(value) {
  // Tauri recebe a codificação base64 do .pub completo; minisign usa a linha interna.
  const text = value.trim().startsWith('untrusted comment:') ? value.trim() : minisignText(value, 'Chave pública');
  const line = text.split(/\r?\n/)[1];
  assert(line && Buffer.from(line, 'base64').length === 42, 'Chave pública inválida');
  return line;
}

export async function validateRelease({ store, channel, version, candidate, pubkey, baseUrl, tag }) {
  assert(['test', 'production'].includes(channel), 'Canal inválido');
  assert(semver.valid(version) === version, 'Versão SemVer inválida');
  assert(channel === 'test' ? /^\d+\.\d+\.\d+-test\.(0|[1-9]\d*)$/.test(version) : /^\d+\.\d+\.\d+$/.test(version), 'Versão incompatível com canal');
  assert(tag === `v${version}`, 'Tag divergente da versão');
  const base = new URL(baseUrl);
  assert(base.protocol === 'https:' && !base.username && !base.password && !base.search && !base.hash && base.pathname === '/', 'Base deve ser origem HTTPS sem credenciais');
  assert(candidate?.version === version && typeof candidate.notes === 'string'
    && typeof candidate.pub_date === 'string' && Number.isFinite(Date.parse(candidate.pub_date)), 'Manifesto candidato inválido');
  const file = `HORUS_${version}_x64-setup.exe`;
  const prefix = `${channel}/releases/${version}/`;
  const platform = candidate.platforms?.['windows-x86_64'];
  assert(platform?.url === new URL(prefix + file, base).href, 'URL deve apontar ao instalador do mesmo canal, versão e origem');
  const previousResponse = await store.get(`${channel}/latest.json`);
  const previousEtag = previousResponse.status === 404 ? null : previousResponse.headers.get('etag');
  if (previousResponse.status !== 404) {
    assert(previousEtag, 'Manifesto atual sem ETag; publicação segura indisponível');
    const previous = await json(previousResponse, 'Manifesto atual');
    assert(semver.valid(previous.version) && semver.gt(version, previous.version), 'Publicação duplicada ou regressiva recusada');
  }
  const release = await json(await store.get(prefix + 'release.json'), 'release.json');
  assert(release.version === version && /^[a-f0-9]{64}$/.test(release.sha256)
    && Number.isSafeInteger(release.size) && release.size > 0 && /^[a-f0-9]{40}$/.test(release.commit)
    && release.notes === candidate.notes && release.pub_date === candidate.pub_date, 'Metadados da release divergentes');
  const sigResponse = await store.get(prefix + file + '.sig');
  assert(sigResponse.ok, 'Assinatura ausente');
  const signature = (await sigResponse.text()).trim();
  assert(signature.length <= 16 * 1024 && signature === platform.signature, 'Assinatura divergente do manifesto');
  const sigText = minisignText(signature, 'Assinatura');
  const artifact = await store.get(prefix + file);
  assert(artifact.ok && artifact.body, 'Instalador ausente');
  const directory = await mkdtemp(join(tmpdir(), 'horus-release-validation-'));
  try {
    const path = join(directory, 'installer.bin');
    const output = await open(path, 'wx', 0o600);
    const hash = createHash('sha256');
    let size = 0;
    try {
      for await (const chunk of artifact.body) {
        hash.update(chunk); size += chunk.length;
        assert(size <= release.size, 'Tamanho do instalador divergente');
        // FileHandle.write pode escrever parcialmente; writeFile completa cada chunk.
        await output.writeFile(chunk);
      }
    } finally { await output.close(); }
    assert(size === release.size && hash.digest('hex') === release.sha256, 'SHA-256/tamanho do instalador divergentes');
    const verifier = await MinisignVerifier.create(publicKey(pubkey));
    let verified;
    try { verified = await verifier.verifyFilepath(path, await writeSignature(directory, sigText)); }
    catch { throw new Error('Assinatura minisign inválida'); }
    assert(verified.ok, 'Assinatura minisign inválida');
    return { manifest: JSON.stringify(candidate, null, 2) + '\n', previousEtag, release };
  } finally { await rm(directory, { recursive: true }); }
}

async function writeSignature(directory, text) {
  const path = join(directory, 'signature.minisig');
  const file = await open(path, 'wx', 0o600);
  try { await file.writeFile(text); } finally { await file.close(); }
  return path;
}

export async function optionsFromCLI(argv) {
  const args = argumentsForRelease(argv);
  let candidate;
  try { candidate = JSON.parse(await readFile(args.candidate, 'utf8')); } catch { throw new Error('Arquivo candidato inválido'); }
  return { store: createStore(args.channel, args.directory), channel: args.channel, version: args.version,
    candidate, pubkey: await readFile(args.pubkey, 'utf8'), baseUrl: args['base-url'], tag: args.tag };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  optionsFromCLI(process.argv.slice(2)).then(validateRelease).then(() => console.log('Release validada; latest.json NÃO foi escrito.'))
    .catch(() => { console.error('Validação recusada; confira metadados, artefatos, assinatura, canal, tag e versão.'); process.exitCode = 1; });
}
