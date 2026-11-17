import { randomBytes, createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const wrangler = join(root, 'node_modules/wrangler/bin/wrangler.js');
const hashRE = /^[a-f0-9]{64}$/;
const key = hash => `installation:${hash}`;
export function channelName(channel) {
  if (!['test', 'production'].includes(channel)) throw new Error('Canal inválido');
  return channel;
}

// Somente hash e metadados seguem para o subprocesso; nunca o segredo original.
export function wranglerAdmin(channel, remote = false) {
  channelName(channel);
  const mode = remote ? '--remote' : '--local';
  async function run(args) {
    try {
      const result = await exec(process.execPath, [wrangler, ...args, '--env', channel, mode], {
        cwd: root, env: { ...process.env, CI: 'true', WRANGLER_SEND_METRICS: 'false' }, maxBuffer: 8 * 1024 * 1024,
      });
      return result.stdout;
    } catch { throw new Error('Operação administrativa falhou; confira autenticação, bindings e permissões (saída omitida)'); }
  }
  async function withFile(value, action) {
    const directory = await mkdtemp(join(tmpdir(), 'horus-token-metadata-'));
    try {
      const path = join(directory, 'metadata.json');
      await writeFile(path, value, { mode: 0o600 });
      return await action(path);
    } finally { await rm(directory, { recursive: true }); }
  }
  return {
    async get(hash) {
      if (!hashRE.test(hash)) throw new Error('ID inválido');
      const text = await run(['kv', 'key', 'get', key(hash), '--binding', 'INSTALLATIONS']);
      try { return JSON.parse(text); } catch { throw new Error('Instalação ausente ou registro inválido'); }
    },
    async put(hash, value) {
      if (!hashRE.test(hash)) throw new Error('ID inválido');
      await withFile(JSON.stringify(value), path => run(['kv', 'key', 'put', key(hash), '--binding', 'INSTALLATIONS', '--path', path]));
    },
    async revokeMarker(hash) {
      if (!hashRE.test(hash)) throw new Error('ID inválido');
      const bucket = channel === 'test' ? 'horus-updates-test' : 'horus-updates-prod';
      await withFile('{}', path => run(['r2', 'object', 'put', `${bucket}/${channel}/revoked/${hash}`, '--file', path]));
    },
    async list() {
      const result = JSON.parse(await run(['kv', 'key', 'list', '--binding', 'INSTALLATIONS', '--prefix', 'installation:']));
      if (!Array.isArray(result)) throw new Error('Listagem KV inválida');
      return result.map(entry => entry.name.slice('installation:'.length)).filter(id => hashRE.test(id));
    },
  };
}

export async function issue(store, channel, label) {
  channelName(channel);
  if (typeof label !== 'string' || !label.trim() || label.length > 120 || /[\x00-\x1f\x7f]/.test(label)) throw new Error('Rótulo inválido');
  const token = randomBytes(32).toString('base64url');
  const id = createHash('sha256').update(token).digest('hex');
  await store.put(id, { label: label.trim(), channel, created: new Date().toISOString(), revoked: false });
  return { id, token };
}

export async function revoke(store, channel, id) {
  channelName(channel);
  if (!hashRE.test(id)) throw new Error('ID inválido');
  // Primeiro a barreira forte. Mesmo falha/atraso na leitura KV não reabre acesso.
  await store.revokeMarker(id);
  try {
    const record = await store.get(id);
    if (record.channel !== channel) throw new Error('Canal incorreto');
    await store.put(id, { ...record, revoked: true });
  } catch { throw new Error('Acesso revogado no R2; atualização da flag KV falhou, repita a revogação pelo mesmo ID'); }
}

export async function list(store, channel) {
  channelName(channel);
  const records = [];
  for (const id of await store.list()) {
    const value = await store.get(id);
    if (value.channel === channel) records.push({ id, label: value.label, channel, created: value.created, revoked: value.revoked });
  }
  return records;
}

async function main() {
  const [command, channel, ...args] = process.argv.slice(2);
  const remote = args.includes('--remote');
  const positional = args.filter(arg => arg !== '--remote');
  const store = wranglerAdmin(channel, remote);
  if (command === 'issue' && positional.length === 1) {
    if (!process.stdout.isTTY) throw new Error('Emita em terminal interativo; saída redirecionada/CI recusada para proteger o token');
    const value = await issue(store, channel, positional[0]);
    // ÚNICA emissão do segredo, depois de persistir exclusivamente o hash.
    process.stdout.write(`ID: ${value.id}\nToken (exibido somente agora; guarde no cofre): ${value.token}\n`);
  } else if (command === 'revoke' && positional.length === 1) {
    await revoke(store, channel, positional[0]);
    console.log('Acesso revogado; marcador R2 e flag KV gravados.');
  } else if (command === 'list' && positional.length === 0) {
    console.log(JSON.stringify(await list(store, channel), null, 2));
  } else throw new Error('Uso: npm run admin -- issue|revoke|list test|production [rótulo|ID] [--remote]');
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
