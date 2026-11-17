# HOR-163 B — pipeline de release Windows

Data: 17/11/2026. Branch: `yannbastos/rel-b-pipeline`.
Commit inicial: `f7fe915646b4aab8aa22942ee2de6501e540013b`.
Revisão do Maestro (aprovações): `79cc983bbf42ef4e3051088dff4db50637cf5977`.
Base origin/main local: `0a1e4cea951fdbf5bf8e64d2ee9d10a42e410e94`.
Estado: implementação e ensaios locais aprovados; **release real não executada**.

## Entrega e limites

Somente os arquivos atribuídos a B foram alterados, em commit local Conventional
Commits: `.github/workflows/release.yml`, `scripts/release/**`,
`scripts/product-version.mjs`, seu teste e
`frontend/src-tauri/tauri.release.conf.json`. Worktree limpo depois do commit.
Nenhum push, PR, merge, rebase, alteração de main, publicação ou ação na conta
Cloudflare. Não foi executado app, instalador, atualizador, inferência ou acesso
ao banco/cofre operacional. Não foi criada chave de produção nem chave persistente
de teste; o dry-run gerou e removeu apenas uma chave efêmera de teste sob TEMP.

## Workflow

Gatilho exclusivo `push.tags: ['v*']`, sem gatilho de commits/PR/manual/schedule.
Tags estáveis `vX.Y.Z` mapeiam para production e `vX.Y.Z-test.N` para test; demais
tags são recusadas pela guarda, sem bump automático. Todos os jobs usam
windows-latest, timeouts, actions fixadas por SHA e `contents: read`; checkout não
persiste credencial. Concorrência serializa por canal sem cancelar execução ativa.

Jobs: verify-version → test → build → sign → publish (inclui pós-verificação).
A guarda exige igualdade entre tag/tauri.conf.json/package.json/Cargo.toml, SemVer
válido, limites NSIS de 16 bits, canal correto, versão estritamente maior e ausência
dos três objetos da versão no Worker autenticado. HTTP 401/403/erro não é bootstrap
de canal vazio; redirects são recusados. Artefatos órfãos também impedem repetição.

Test define backend completo em um subprocesso pytest por módulo, incluindo
test_network_inventory, snapshot de API, test_supply_chain e test_endpoint_auth*,
build/Playwright do frontend e cargo test --locked. Para compile dos testes Tauri,
cria só fixture textual no diretório resources ignorado, como necessário no teste
do C; nenhum executável fictício é usado como produto. O job build é outro runner
e gera o backend real por build-backend.ps1 antes de empacotar o NSIS.

Build usa overlay e createUpdaterArtifacts com `tauri build --no-sign`, sem
Environment nem secrets, produzindo somente o setup e o overlay público. O CLI
Tauri 2.12.5 pula também a assinatura updater com esse flag, conforme fonte oficial
conferido em crates/tauri-cli/src/bundle.rs, função sign_updaters. O job sign, depois
dos testes/build e da aprovação, confere a igualdade do overlay público com a
configuração do Environment, assina com tauri signer --app-version e verifica
minisign/versão usando minisign-verify 0.3.0, dependência
já usada pelo updater, num utilitário isolado em scripts/release. Não há criptografia
própria nem confusão com Authenticode. Saída dos subprocessos sensíveis é capturada
e não impressa; secrets entram exclusivamente por ambiente/secrets do GitHub.

Publish exige Environment por canal; production depende de required reviewers e
consulta a API do GitHub, recusando publicação se essa proteção estiver ausente.
O YAML não cria nem configura o Environment. Somente sign/publish o usam; planejar
duas aprovações sequenciais de production (antes de sign e de publish), sem depender
de eventual reutilização da aprovação pela interface. Build não exige aprovação.
Chave/senha são referenciadas só em sign; credenciais R2, só em publish, nunca antes
da aprovação do respectivo job. Test não deve ter required reviewers e exige zero
aprovações humanas. Não foi alterada configuração do GitHub.

## Publicação e integração A

Layout fixo mantido: <channel>/releases/<version>/HORUS_<version>_x64-setup.exe,
.exe.sig, release.json; <channel>/latest.json no formato Tauri v2.
Metadados incluem versão, SHA-256, tamanho exato, notas, pub_date e commit.

O CI usa AWS CLI/S3 direto no endpoint R2, credencial mínima para o bucket do
Environment. Objetos imutáveis usam If-None-Match:*. Os três objetos são relidos e
comparados antes de invocar validateRelease exportado por
infra/cloudflare/validate-release.mjs, com store.get retornando Response/ETag reais.
Ausência do arquivo/export/dependências bloqueia antes de upload. Monotonia e
conteúdo de latest são rechecados após a validação. Latest recebe **um único PUT
final**, com If-Match do ETag consultado ou If-None-Match:* no primeiro manifesto.
Não há fallback/retry incondicional. Falha pode deixar objetos órfãos, nunca latest
anunciando release incompleta; limpeza/recuperação não é automatizada.

Pós-publicação baixa latest e os três objetos pelo Worker HTTPS com Bearer do CI,
conferindo manifesto, metadados, hash/tamanho, assinatura e vínculo da versão.
O dry-run integrado usou o validador real de A no worktree irmão; a árvore final
precisa conter os commits de A e B. Nenhum arquivo de A foi editado.

## Overlay C e decisão final de WebView2

Aplicado o contrato C: HORUS_UPDATE_CHANNEL → plugins.horusUpdater.channel;
HORUS_UPDATE_BASE_URL (origem HTTPS) + /<channel>/latest.json → updater.endpoints;
HORUS_UPDATER_PUBKEY → updater.pubkey; requireSignedVersion:true,
dangerousInsecureTransportProtocol:false e installMode:passive. Sem token no JSON.
Base tauri.conf.json não foi alterada; updater segue ausente no build padrão.

Conforme duas mensagens explícitas do Maestro, o overlay final usa
bundle.windows.webviewInstallMode={type:downloadBootstrapper,silent:true}, com NSIS
currentUser. **Substitui** offlineInstaller sugerido inicialmente em C-tauri.md;
nenhum asset offline foi preparado. Motivo transmitido pelo Maestro: instalador
menor, pois o updater baixa o mesmo setup a cada versão; o app exigirá internet com
Supabase, e o bootstrapper só necessita rede adicional quando falta WebView2.
Não foi alterado banco/arquitetura do produto nesta entrega.

## Tamanho e upload

Artefato do updater Tauri v2 = o próprio setup.exe, sem delta/ZIP. Evidências
anteriores do repositório: mínimo ~16,84 MiB e transcrição ~72,90 MiB comprimidos;
conteúdo expandido first-video ~1,8 GiB. O workflow mantém o perfil mínimo existente,
sem habilitar first-video implicitamente. O setup desta entrega não foi construído,
portanto tamanho final/bootstrapper não foi medido; release.json registra bytes
reais do build futuro, sem estimativa usada como resultado.

O teto de wrangler r2 object put é 300 MiB; o CI **não usa wrangler**, usa S3
PutObject condicional, inclusive acima de 300 MiB. Guarda recusa objeto acima de
5 GiB (limite single PUT); acima disso exige implementação/teste multipart, sem
remover proteção contra duplicatas. Limites confirmados na documentação oficial:
[Upload objects](https://developers.cloudflare.com/r2/objects/upload-objects/) e
[Limits](https://developers.cloudflare.com/r2/platform/limits/).
Não foram feitos uploads, criação de recursos, login ou deploy Cloudflare.

## Verificações executadas

- node --test scripts/product-version.test.mjs scripts/release/*.test.mjs:
  **12 aprovados, 0 falhas/ignorados**, incluindo limites de upload, overlay,
  SemVer/NSIS, canais, versões, falhas de assinatura, órfão, artefato perdido,
  concorrência e CAS. Log: B-node-tests.log nesta pasta.
- node scripts/release/dry-run.mjs ../rel-a-worker/infra/cloudflare/validate-release.mjs:
  **aprovado**, com assinatura minisign real sobre bytes sintéticos, stub HTTP
  loopback autenticado e validador real de A. Prova publicação feliz/hash, recusa
  sem token, adulteração, versão assinada diferente, duplicata, regressão, artefato
  ausente e falha do validador sem latest; PUT único de latest ao final.
  Log: B-dry-run.log nesta pasta. Também aprovado antes sem o validador opcional.
- actionlint 1.7.7 .github/workflows/release.yml: **aprovado, sem diagnósticos**.
  Binário oficial em TEMP, ZIP conferido com checksum SHA-256 do release público;
  primeira tentativa de download falhou por timeout TLS, segunda concluiu.
  B-actionlint.log vazio significa ausência de diagnósticos.
- Parse PowerShell dos dois scripts, rustfmt do verificador e git diff --cached
  --check: **aprovados**. Compilação do utilitário Rust executada pelo dry-run.
- Dependências locais do frontend preparadas explicitamente por npm ci
  --prefix frontend --ignore-scripts, usando lockfile, sem alterar manifestos.

Não executados: workflow real, backend/Playwright/cargo test do produto nesta
entrega B, build NSIS de release, instalação/atualização real, WebView2, Windows
limpo, upload/pós-verificação remotos. Os checks do produto estão definidos no
workflow, mas isso não constitui resultado de execução nem homologação do produto.

## Pendências do usuário / Maestro

Configurar Environments, revisores, variáveis por canal e secrets mínimos de R2,
token CI e assinatura; aprovar custódia/chave de produção e primeira publicação.
Procedimento curto com gh secret set via stdin/prompt oculto está versionado em
scripts/release/README.md, sem valores reais. Geração persistente é exclusivamente
de TESTE em LOCALAPPDATA/Horus/release-keys/test, recusa sobrescrita/junction;
o script não foi executado nessa pasta nesta fase.

Integrar A/C/D, conferir inventário de rede/documentação de integração no escopo
do Maestro e validar Actions/instalador/atualização em ambiente autorizado. Não
declarar HOR-163 inteiro concluído a partir deste relatório local.

## Revisão pequena solicitada pelo Maestro — aprovações

Commit adicional `79cc983bbf42ef4e3051088dff4db50637cf5977`, sem reescrever o
commit anterior. Removido Environment e todas as referências a secrets do build;
configuração pública vem de UPDATE_TEST/PRODUCTION_BASE_URL e novas variáveis
UPDATE_TEST_UPDATER_PUBKEY / UPDATE_PRODUCTION_UPDATER_PUBKEY no repositório.
O build usa --no-sign; collect não exige .sig, gerada somente por sign. O artifact
intermediário transporta setup + tauri.generated.json, ambos sem segredo. Sign tem
needs explícito de verify-version, test e build; falha de qualquer um bloqueia-o.
Mantidos Environments protegidos em sign e publish, com duas etapas de aprovação
de produção documentadas no README. O token de leitura do pré-check é separado e
não concede assinatura ou escrita R2.

Conferência adicional impede assinar se o overlay do build divergir do Environment
em chave pública, canal ou endpoint. Coberta no teste de overlay já existente, com
cenários negativos para os três valores; total continua 12 testes.

Verificações repetidas: node --test scripts/product-version.test.mjs
scripts/release/*.test.mjs: **12 aprovados, 0 falhas/ignorados**;
actionlint 1.7.7: **aprovado**; git diff --cached --check: **aprovado**.
Logs B-revisao-node-tests.log e B-revisao-actionlint.log nesta pasta.
Fonte técnica: [CLI Tauri 2.12.5 — sign_updaters](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.5/crates/tauri-cli/src/bundle.rs).
Não executados build/Actions/publicação/instalador reais nesta revisão; mesmos
bloqueios de secrets, proteção dos Environments e validação autorizada permanecem.
