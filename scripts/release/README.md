# Pipeline de release Windows (HOR-163 B)

Somente tags `vX.Y.Z` (production) e `vX.Y.Z-test.N` (test); nenhum bump automático,
commit na main, release do GitHub ou deploy Cloudflare. Versões devem coincidir nos
três manifestos do produto. O build padrão continua sem updater; o overlay de release
é gerado com `HORUS_UPDATE_BASE_URL` (origem HTTPS, sem canal no caminho),
`HORUS_UPDATE_CHANNEL` e `HORUS_UPDATER_PUBKEY`. O template versionado habilita
`createUpdaterArtifacts`, NSIS `currentUser` e WebView2
`downloadBootstrapper` com `silent:true`, conforme decisão final do Maestro.
O gerador grava `plugins.horusUpdater.channel`, endpoint, chave pública,
`requireSignedVersion:true`, transporte inseguro `false` e instalação `passive`.
Nenhum token entra no overlay. A escolha substitui `offlineInstaller` proposto no
relatório inicial de C: mantém o setup menor, pois o updater baixa esse mesmo arquivo
a cada versão; só exige rede adicional quando o WebView2 estiver ausente.

## Validação local sem nuvem

Requer Node 22, dependências do frontend já instaladas com `npm ci --prefix frontend`,
Rust e Cargo. Não instala nem executa o aplicativo/NSIS. Não usa cofre, banco, dados
operacionais, credenciais Cloudflare ou chave de produção.

```powershell
node --test scripts/product-version.test.mjs scripts/release/*.test.mjs
node scripts/release/dry-run.mjs
# Opcional: integração com o validador A já disponível (nenhuma nuvem):
node scripts/release/dry-run.mjs infra/cloudflare/validate-release.mjs
actionlint .github/workflows/release.yml
```

O dry-run gera chave efêmera de TESTE sob TEMP, descarta a saída privada do signer,
assina bytes sintéticos com `tauri signer --app-version`, valida minisign usando a
biblioteca auditada já usada pelo updater e testa um stub HTTP autenticado loopback.
Prova sucesso, hash, assinatura, vínculo de versão, adulteração, duplicata, regressão,
artefato ausente, falha do validador e PUT único de latest após os artefatos. Remove
somente a pasta TEMP que ele próprio criou. Não equivale a execução real no R2,
Miniflare, instalador, WebView2, Authenticode ou validação da jornada instalada.

## Configuração do usuário (não executada nesta entrega)

Criar os GitHub Environments **test** e **production**; production deve ter revisor
obrigatório, preferencialmente impedir autoaprovação e bypass administrativo, e aceitar
somente tags estáveis. test deve aceitar somente tags de teste e não exigir aprovação.
O YAML não configura proteção de Environment: o job publish consulta a API do GitHub e
**recusa publicar em production se não houver required reviewers**. Build/sign também
usam o Environment porque o Tauri exige a chave ao criar artefatos updater; pode haver
aprovação antes do build e novamente na publicação. Apenas publicação aprovada é efetivada.
O GITHUB_TOKEN precisa somente `contents: read`; a consulta de proteção deve ser legível
no repositório público. Não aumentar permissões automaticamente caso a API a recuse.

Em cada Environment, cadastrar variáveis `HORUS_UPDATE_BASE_URL`,
`HORUS_UPDATER_PUBKEY`, `HORUS_R2_BUCKET`, `HORUS_R2_ENDPOINT` e
`HORUS_RELEASE_NOTES` (texto da release, obrigatório). Endpoint R2:
`https://<account-id>.r2.cloudflarestorage.com`. Buckets, KV, rotas, chaves e tokens
devem ser separados por canal. R2 usa acesso de leitura/escrita de objetos **somente
ao bucket do canal**, sem permissão administrativa. AWS CLI v2, gh e Rust são usados
no runner Windows; nenhuma instalação com elevação é feita por estes scripts.

Secrets por Environment: `TAURI_SIGNING_PRIVATE_KEY` (conteúdo base64 Tauri),
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, `HORUS_CI_READ_TOKEN`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`. O token de CI é token de instalação/leitura do Worker do canal,
nunca credencial administrativa de emissão/revogação. No repositório, cadastrar também
`UPDATE_TEST_CI_TOKEN`, `UPDATE_PRODUCTION_CI_TOKEN`, e variáveis
`UPDATE_TEST_BASE_URL`, `UPDATE_PRODUCTION_BASE_URL` para a guarda anterior aos jobs.
Esses valores precisam corresponder aos de cada Environment. Ausência não faz fallback
para outro canal. Não colocar segredo em variável pública, argumento CLI, chat ou log.

Exemplo seguro de envio da chave **de teste** via stdin (não executar com transcript):

```powershell
# Executar o gerador só quando autorizado a gravar a pasta de chaves dedicada.
./scripts/release/generate-test-signing-key.ps1
# Gera somente TESTE em %LOCALAPPDATA%/Horus/release-keys/test/updater-test.key;
# recusa sobrescrita e imprime somente a chave pública.
Get-Content -Raw "$env:LOCALAPPDATA/Horus/release-keys/test/updater-test.key" |
  gh secret set TAURI_SIGNING_PRIVATE_KEY --env test --repo yannbastos/horus
# Para outros secrets, usar o prompt oculto de gh secret set, sem --body literal:
gh secret set HORUS_CI_READ_TOKEN --env test --repo yannbastos/horus
gh secret set UPDATE_TEST_CI_TOKEN --repo yannbastos/horus
```

Nunca gerar chave de produção com estes scripts. Produção aguarda aprovação do
procedimento de custódia e importação segura pelo usuário. Assinatura minisign do
updater não é Authenticode e não elimina avisos SmartScreen. Segredos do GitHub são
mascarados pelo runner; subprocessos de assinatura/verificação/publicação capturam
e descartam saída sensível. Nenhum arquivo privado entra nos artifacts do workflow.

## Integração com A e escrita atômica

O pipeline chama, **depois** de subir e reler os três objetos imutáveis no R2, e
**antes** de gravar latest:

```javascript
// Import dinâmico de infra/cloudflare/validate-release.mjs:
await validateRelease({
  store: { get: key => storage.response(key) }, // Response com bytes e ETag reais do R2
  channel, version, candidate: bundle.latest,
  pubkey: process.env.HORUS_UPDATER_PUBKEY,
  baseUrl: origin, tag: process.env.GITHUB_REF_NAME,
});
```

A pasta contém `HORUS_<version>_x64-setup.exe`, `.exe.sig`, `release.json`,
`latest.candidate.json` e `candidate.json`. O validador A recebe o storage R2 pelo
contrato exportado, chave pública e metadados; confere conteúdo/hash/tamanho/minisign,
URL e monotonia. O verificador Rust de B exige adicionalmente a versão no comentário
assinado antes de qualquer upload. A não escreve latest; exceção bloqueia. Arquivo ou
export ausente bloqueia **antes de qualquer upload**. O workflow instala dependências
de `infra/cloudflare` pelo lockfile. O dry-run integrado foi executado com o validador
real de A no worktree irmão; a integração final depende de ambos os commits na mesma
árvore. O pré-check e a pós-verificação usam exclusivamente o Worker HTTPS autenticado.

Objetos de release usam PUT condicional `If-None-Match: *` (sem sobrescrita).
`<channel>/latest.json` usa um único PutObject, depois de todos os checks, com
rechecagem de monotonia/conteúdo e CAS: `If-Match` com o ETag consultado ou
`If-None-Match:*` no primeiro manifesto. A escrita falha se o R2 rejeitar a condição;
nunca há retry incondicional. Concorrência serializa por canal dentro do repositório.
Falha de upload/validação pode deixar objetos órfãos:
retry da mesma versão é recusado, sem apagar/sobrescrever; recuperação requer decisão
do administrador. Nenhum latest é anunciado nessa falha. Pós-publicação baixa os
três objetos e latest pelo Worker com Bearer e verifica hash/tamanho/minisign.

## Tamanho do setup e transporte

O artefato Tauri v2 é o próprio `HORUS_<version>_x64-setup.exe`, acompanhado de `.sig`;
não é ZIP nem delta. As medições anteriores em `docs/adr-atualizador.md` e
`docs/proposta-nucleo-recursos.md` registram aproximadamente 16,84 MiB (mínimo),
72,90 MiB (transcrição) e cerca de 1,8 GiB no conteúdo expandido do first-video.
Não foi reconstruído nem medido o setup desta entrega; o bootstrapper acrescenta
overhead pequeno, mas o tamanho final só se confirma no build. Este workflow usa
o perfil mínimo existente; não habilita first-video nem baixa seus recursos.
`release.json.size` registra o número exato de bytes do setup efetivamente gerado.

`wrangler r2 object put` tem teto de 300 MiB e **não é usado** pelo pipeline.
O CI envia diretamente por AWS CLI/S3 `PutObject` ao endpoint privado R2, com PUT
condicional. Assim também aceita setups acima de 300 MiB, até 5 GiB por objeto;
o gerador recusa tamanho maior. Acima de 5 GiB seria necessário implementar e testar
multipart antes da publicação, sem remover a guarda de duplicata. Limites oficiais:
[uploads R2](https://developers.cloudflare.com/r2/objects/upload-objects/) e
[limites R2](https://developers.cloudflare.com/r2/platform/limits/).

Antes de uma release real, o Maestro deve validar a jornada instalada e completar
os demais critérios da governança em ambiente autorizado. Esta entrega não executa
Actions, não cria Environments/secrets, não publica e não altera a conta Cloudflare.
