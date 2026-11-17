import { AwsClient } from 'aws4fetch';
import { readFile, writeFile, rename, mkdir, open, unlink } from 'node:fs/promises';
import { dirname, resolve, sep } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { channelName } from './admin/tokens.mjs';

export function argumentsForRelease(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--channel', '--version', '--candidate', '--pubkey', '--base-url', '--directory', '--tag'].includes(argv[i]) || !argv[i + 1]) throw new Error('Argumentos inválidos');
    args[argv[i].slice(2)] = argv[i + 1];
  }
  for (const name of ['channel', 'version', 'candidate', 'pubkey', 'base-url', 'tag']) if (!args[name]) throw new Error(`Falta --${name}`);
  channelName(args.channel);
  return args;
}

export function createStore(channel, directory, env = process.env) {
  channelName(channel);
  if (directory) {
    const root = resolve(directory);
    function path(key) {
      const target = resolve(root, key);
      if (!target.startsWith(root + sep) || !key.startsWith(`${channel}/`)) throw new Error('Chave inválida');
      return target;
    }
    return {
      async get(key) {
        try {
          const value = await readFile(path(key));
          return new Response(value, { headers: { ETag: `"${createHash('sha256').update(value).digest('hex')}"` } });
        } catch (error) { if (error.code === 'ENOENT') return new Response(null, { status: 404 }); throw error; }
      },
      async putLatest(key, body, etag) {
        const target = path(key);
        await mkdir(dirname(target), { recursive: true });
        // Lock exclusivo somente para o simulador de diretório; remoto usa CAS R2.
        let lock;
        try { lock = await open(`${target}.lock`, 'wx'); } catch { throw new Error('Publicação concorrente recusada'); }
        const temporary = `${target}.${randomUUID()}.tmp`;
        try {
          const current = await this.get(key);
          if ((current.status === 404 ? null : current.headers.get('etag')) !== etag) throw new Error('Publicação concorrente recusada');
          await writeFile(temporary, body);
          await rename(temporary, target);
        } finally {
          await unlink(temporary).catch(() => {});
          await lock.close(); await unlink(`${target}.lock`);
        }
      },
    };
  }
  for (const key of ['CLOUDFLARE_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY']) if (!env[key]) throw new Error(`Falta variável ${key}`);
  if (!/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID)) throw new Error('Conta inválida');
  const aws = new AwsClient({ accessKeyId: env.R2_ACCESS_KEY_ID, secretAccessKey: env.R2_SECRET_ACCESS_KEY,
    service: 's3', region: 'auto', retries: 0 });
  const bucket = channel === 'test' ? 'horus-updates-test' : 'horus-updates-prod';
  function url(key) {
    if (!key.startsWith(`${channel}/`) || key.includes('..')) throw new Error('Chave inválida');
    return `https://${env.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com/${bucket}/${key.split('/').map(encodeURIComponent).join('/')}`;
  }
  return {
    async get(key) {
      const response = await aws.fetch(url(key), { redirect: 'error' });
      if (!response.ok && response.status !== 404) throw new Error(`Leitura R2 falhou (${response.status})`);
      return response;
    },
    async putLatest(key, body, etag) {
      const response = await aws.fetch(url(key), { method: 'PUT', body, redirect: 'error', headers: {
        'Content-Type': 'application/json', 'Cache-Control': 'no-store',
        ...(etag ? { 'If-Match': etag } : { 'If-None-Match': '*' }),
      } });
      if (!response.ok) throw new Error(`Publicação recusada (${response.status}); nenhum retry automático`);
    },
  };
}
