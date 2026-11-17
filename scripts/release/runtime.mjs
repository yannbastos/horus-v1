import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

export const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const tauri = path.join(root, 'frontend/node_modules/@tauri-apps/cli/tauri.js');
export const verifier = path.join(root, 'frontend/src-tauri/target/release-tools/debug', `horus-release-signature-verifier${process.platform === 'win32' ? '.exe' : ''}`);
export function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, encoding: 'utf8', stdio: 'pipe', ...options });
  if (result.error || result.status !== 0) throw Error(`Comando falhou: ${path.basename(command)} (saída omitida para preservar segredos).`);
  return result.stdout;
}
export function buildVerifier() {
  run('cargo', ['build', '--locked', '--manifest-path', 'scripts/release/signature-verifier/Cargo.toml', '--target-dir', 'frontend/src-tauri/target/release-tools']);
}
export function signatureCheck(publicKey, installer, signature, version) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'horus-release-verify-'));
  try {
    for (const [name, content] of [['key.pub', publicKey], ['setup.exe', installer], ['setup.exe.sig', signature]]) fs.writeFileSync(path.join(temp, name), content);
    run(verifier, [path.join(temp, 'key.pub'), path.join(temp, 'setup.exe'), path.join(temp, 'setup.exe.sig'), version]);
  } finally { fs.rmSync(temp, { recursive: true }); }
}
