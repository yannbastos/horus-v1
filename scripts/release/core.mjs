// Contratos puros de release; sem acesso à nuvem ou a credenciais.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { compare } from '../product-version.mjs';

export function releaseTag(tag) {
  const match = /^v((0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-test\.(0|[1-9]\d*))?)$/.exec(tag);
  if (!match) throw Error('Tag deve ser vX.Y.Z ou vX.Y.Z-test.N, sem metadados.');
  // NSIS representa os quatro componentes de versão em inteiros de 16 bits.
  if (match.slice(2).filter(Boolean).some(n => BigInt(n) > 65535n)) throw Error('Versão excede o limite NSIS (65535).');
  return { version: match[1], channel: match[5] === undefined ? 'production' : 'test' };
}

export function guard({ tag, versions, latest, exists = false }) {
  const result = releaseTag(tag);
  if (versions.length !== 3 || versions.some(v => v !== result.version)) throw Error('Tag diverge de tauri.conf.json/package.json/Cargo.toml.');
  if (exists) throw Error('Release duplicada no canal.');
  if (latest !== undefined) {
    const previous = releaseTag(`v${latest}`);
    if (previous.channel !== result.channel) throw Error('Manifesto pertence a outro canal.');
    if (compare(result.version, latest) <= 0) throw Error('Release duplicada ou regressiva.');
  }
  return result;
}

export function productVersions(root) {
  const json = file => JSON.parse(fs.readFileSync(path.join(root, file), 'utf8')).version;
  const cargo = fs.readFileSync(path.join(root, 'frontend/src-tauri/Cargo.toml'), 'utf8');
  const section = cargo.match(/^\[package\]\s*\r?\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m)?.[1];
  return [json('frontend/src-tauri/tauri.conf.json'), json('frontend/package.json'), section?.match(/^version\s*=\s*"([^"]+)"/m)?.[1]];
}

export function baseURL(raw, local = false) {
  const url = new URL(raw);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw Error('Base URL deve conter somente origem, sem credenciais.');
  if (url.protocol !== 'https:' && !(local && url.protocol === 'http:' && url.hostname === '127.0.0.1')) throw Error('HTTPS obrigatório; HTTP apenas em ensaio loopback explícito.');
  return url.origin;
}

export function overlay(env, template) {
  const channel = env.HORUS_UPDATE_CHANNEL;
  if (!['test', 'production'].includes(channel)) throw Error('Canal inválido.');
  const origin = baseURL(env.HORUS_UPDATE_BASE_URL);
  if (!env.HORUS_UPDATER_PUBKEY?.trim()) throw Error('Chave pública ausente.');
  return { ...template, plugins: { horusUpdater: { channel }, updater: { pubkey: env.HORUS_UPDATER_PUBKEY.trim(), endpoints: [`${origin}/${channel}/latest.json`], requireSignedVersion: true, dangerousInsecureTransportProtocol: false, windows: { installMode: 'passive' } } } };
}

export function assertUploadSize(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > 5 * 1024 ** 3) throw Error('Upload S3 single PUT deve ter entre 1 byte e 5 GiB; acima disso exige pipeline multipart autorizado.');
  return bytes;
}

export const sha256 = data => createHash('sha256').update(data).digest('hex');
export const installerName = version => `HORUS_${version}_x64-setup.exe`;
export const releasePrefix = (channel, version) => `${channel}/releases/${version}`;

export function candidate({ tag, installer, signature, origin, notes, pub_date, commit, local = false }) {
  const { version, channel } = releaseTag(tag);
  origin = baseURL(origin, local);
  if (!Buffer.isBuffer(installer) || !installer.length || !signature?.trim()) throw Error('Artefato ou assinatura ausente.');
  assertUploadSize(installer.length);
  if (!/^[a-f0-9]{40}$/.test(commit) || !notes?.trim() || !pub_date || !Number.isFinite(Date.parse(pub_date))) throw Error('Metadados de release inválidos.');
  const filename = installerName(version);
  const release = { version, sha256: sha256(installer), size: installer.length, notes, pub_date, commit };
  const latest = { version, notes, pub_date, platforms: { 'windows-x86_64': { signature: signature.trim(), url: `${origin}/${releasePrefix(channel, version)}/${filename}` } } };
  return { channel, version, filename, release, latest };
}

// O storage injeta transporte local ou R2. latest só é escrito depois da validação externa.
export async function publish({ storage, bundle, installer, signature, validate, verifySignature }) {
  const { channel, version, filename, release, latest } = bundle;
  const prefix = releasePrefix(channel, version);
  const current = await storage.get(`${channel}/latest.json`);
  const existing = await Promise.all([filename, `${filename}.sig`, 'release.json'].map(name => storage.get(`${prefix}/${name}`)));
  guard({ tag: `v${version}`, versions: [version, version, version], latest: current ? JSON.parse(current.toString()).version : undefined, exists: existing.some(v => v !== null) });
  if (!installer?.length || !signature?.trim()) throw Error('Artefato ou assinatura ausente.');
  if (sha256(installer) !== release.sha256 || installer.length !== release.size || latest.platforms['windows-x86_64'].signature !== signature.trim()) throw Error('Integridade do candidato inválida.');
  await verifySignature(installer, signature, version);
  for (const [name, data] of [[filename, installer], [`${filename}.sig`, signature], ['release.json', JSON.stringify(release)]]) await storage.put(`${prefix}/${name}`, data, true);
  for (const [name, expected] of [[filename, installer], [`${filename}.sig`, Buffer.from(signature)], ['release.json', Buffer.from(JSON.stringify(release))]]) {
    const remote = await storage.get(`${prefix}/${name}`);
    if (!remote || !remote.equals(expected)) throw Error('Artefato remoto ausente ou divergente.');
  }
  await validate(bundle); // Fail closed se o validador do Worker não estiver integrado.
  const refreshed = await storage.get(`${channel}/latest.json`);
  guard({ tag: `v${version}`, versions: [version, version, version], latest: refreshed ? JSON.parse(refreshed.toString()).version : undefined });
  if (current?.toString() !== refreshed?.toString()) throw Error('Manifesto mudou durante a publicação; operação recusada.');
  if (storage.putLatest) await storage.putLatest(`${channel}/latest.json`, JSON.stringify(latest));
  else await storage.put(`${channel}/latest.json`, JSON.stringify(latest), false); // ÚLTIMO: um único PUT.
}
