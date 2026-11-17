import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { optionsFromCLI, validateRelease } from './validate-release.mjs';

export async function publishManifest(options) {
  const result = await validateRelease(options);
  // Última operação: um único PUT condicional e atômico, sem retry em concorrência.
  await options.store.putLatest(`${options.channel}/latest.json`, result.manifest, result.previousEtag);
  return result.release.version;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  optionsFromCLI(process.argv.slice(2)).then(publishManifest).then(version => console.log(`Manifesto ${version} publicado atomicamente.`))
    .catch(() => { console.error('Publicação recusada; latest.json não confirmado. Confira validação, permissões ou concorrência.'); process.exitCode = 1; });
}
