// Fonte canônica: frontend/package.json. Sem dependências externas, Node 20+.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const pattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;
export function compare(a, b) {
  const aa = pattern.exec(a), bb = pattern.exec(b);
  if (!aa || !bb) throw Error('Versão SemVer inválida.');
  for (let i = 1; i <= 3; i++) if (BigInt(aa[i]) !== BigInt(bb[i])) return BigInt(aa[i]) > BigInt(bb[i]) ? 1 : -1;
  if (!aa[4] || !bb[4]) return aa[4] === bb[4] ? 0 : aa[4] ? -1 : 1;
  const x = aa[4].split('.'), y = bb[4].split('.');
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    if (x[i] === undefined || y[i] === undefined) return x[i] === undefined ? -1 : 1;
    if (x[i] === y[i]) continue;
    const nx = /^\d+$/.test(x[i]), ny = /^\d+$/.test(y[i]);
    if (nx && ny) return BigInt(x[i]) > BigInt(y[i]) ? 1 : -1;
    if (nx !== ny) return nx ? -1 : 1;
    return x[i] > y[i] ? 1 : -1;
  }
  return 0;
}

export function synchronize(root, version, allow = false) {
  const canonical = JSON.parse(fs.readFileSync(path.join(root, 'frontend/package.json'), 'utf8')).version;
  compare(canonical, canonical);
  const wanted = version || canonical;
  compare(wanted, wanted); // Validar também quando a permissão de downgrade está explícita.
  if (version && !allow && compare(version, canonical) <= 0) throw Error('Versão menor ou igual; use --allow-non-increasing explicitamente.');
  // Substituições pontuais preservam formatação e não tocam versões de dependências.
  const jsonFiles = ['frontend/package.json', 'frontend/src-tauri/tauri.conf.json',
    ...fs.readdirSync(path.join(root, 'scripts')).filter(n => /^tauri.*\.json$/.test(n)).map(n => `scripts/${n}`)];
  const edits = [];
  for (const file of jsonFiles) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    if (!Object.hasOwn(JSON.parse(text), 'version')) continue;
    edits.push([file, text, text.replace(/("version"\s*:\s*")[^"]+"/, `$1${wanted}"`)]);
  }
  for (const file of ['frontend/src-tauri/Cargo.toml', 'backend/pyproject.toml']) {
    const text = fs.readFileSync(path.join(root, file), 'utf8');
    if (!/^version\s*=\s*"[^"]+"/m.test(text)) throw Error(`Versão ausente: ${file}`);
    edits.push([file, text, text.replace(/^(version\s*=\s*")[^"]+"/m, `$1${wanted}"`)]);
  }
  // Locks e versão exposta pela API também fazem parte do mesmo produto.
  for (const [file, regex] of [
    ['frontend/package-lock.json', /("(?:name": "horus-desktop",\s*")?version"\s*:\s*")[^"]+"/],
    ['frontend/src-tauri/Cargo.lock', /(name = "horus-desktop"\r?\nversion = ")[^"]+"/],
    ['backend/uv.lock', /(name = "horus-backend"\r?\nversion = ")[^"]+"/],
    ['backend/src/horus_backend/api.py', /("hermes_connected"[^\n]+"version": ")[^"]+"/],
  ]) {
    const location = path.join(root, file);
    if (!fs.existsSync(location)) continue;
    const text = fs.readFileSync(location, 'utf8');
    const updated = file.endsWith('package-lock.json')
      ? text.replace(/("version"\s*:\s*")[^"]+"/, `$1${wanted}"`).replace(/("name": "horus-desktop",\s*"version": ")[^"]+"/g, `$1${wanted}"`)
      : text.replace(regex, `$1${wanted}"`);
    edits.push([file, text, updated]);
  }
  const changed = edits.filter(([,before,after]) => before !== after);
  if (!version && changed.length) throw Error(`Versões dessincronizadas: ${changed.map(([f]) => f).join(', ')}`);
  if (version) for (const [file,,text] of changed) fs.writeFileSync(path.join(root, file), text);
  return wanted;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (!args.length || args.some(a => a.startsWith('--') && !['--check', '--allow-non-increasing'].includes(a))) throw Error('Uso: node scripts/product-version.mjs --check | <SemVer> [--allow-non-increasing]');
    console.log(synchronize(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'), args.includes('--check') ? undefined : args.find(a => !a.startsWith('--')), args.includes('--allow-non-increasing')));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
