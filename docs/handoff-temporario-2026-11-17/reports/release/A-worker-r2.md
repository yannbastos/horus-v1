# HOR-163 — A: Worker + R2 privado

Data: 17/11/2026. Branch: `yannbastos/rel-a-worker`.
Base observada: `origin/main` em `0a1e4cea951fdbf5bf8e64d2ee9d10a42e410e94`.
Commit inicial: `0881cc6ac0b8bca0185ebebfa9c6f4ded9d211b1`.
Correção pós-revisão: `5c23bc1a01ab4a173e92b5071c9929723c865a75`.
Escopo versionado: exclusivamente `infra/cloudflare/**` (13 arquivos); worktree limpa após o commit.

## Entrega

- Worker TypeScript com ambientes `test`/`production`, buckets/namespace KV separados, autenticação Bearer de instalação por SHA-256, negação uniforme 403 sem revelar existência e falha fechada em erro KV/R2.
- Manifesto Tauri v2 JSON sem cache; só responde se EXE, assinatura e release.json existirem; URLs limitadas à mesma origem/canal/versão. Artefatos por streaming, HEAD, Range único/sufixo/aberto, 416 em faixa inválida e If-Range por ETag.
- HTTPS obrigatório; HTTP apenas no ambiente `test` em loopback. Sem logs do Worker/observability; limitador local de abuso de 120 requisições/IP/minuto, memória limitada, sem promessa de cota distribuída.
- Admin com Wrangler local por padrão e remoto somente com `--remote`; credencial do admin por login preexistente ou variáveis de ambiente. Emissão de 256 bits com exibição única em TTY (redirecionamento/CI recusados), persistência exclusiva de hash/metadados, listagem e revogação. Subprocessos sem login interativo automático (`CI=true`).
- Validador confere canal, SemVer/tag, progresso de versão, metadados, três objetos, SHA-256/tamanho do EXE, assinatura minisign real e igualdade do `.sig` com o manifesto candidato. Publisher revalida e executa somente então um PUT atômico condicional de latest.json, com If-Match/If-None-Match e sem retry automático.
- README com exatamente 60 linhas: comandos de provisionamento/deploy apenas para o usuário, limites, procedimentos locais e publicação. Dependências/lockfile locais, todos os resolved URLs em registry.npmjs.org.

## Ajuste de contrato explicitamente aprovado

KV tem consistência eventual e sozinho não garante revogação global imediata. Questão enviada ao Maestro (`msg_563c60c59db1`), aprovação recebida em `msg_13f6f0ca429b`:

- Cadastro/hash/flag permanecem no KV.
- Revogação grava primeiro `{}` no R2 privado em `<channel>/revoked/<sha256-do-token>` e depois atualiza flag KV.
- Cada requisição com Bearer no formato esperado consulta HEAD desse marcador antes de servir conteúdo; presença ou falha de consulta nega acesso.
- Token novo pode levar aproximadamente 60 s para valer; revogação vale para as novas requisições após confirmar o marcador. Transferências já iniciadas não podem ser recolhidas.
- Custo adicional: 1 HEAD R2 por requisição com Bearer sintaticamente válido; não há Durable Object nem novo recurso externo. Manifesto soma GET + 3 HEAD; EXE soma HEAD + GET.
- Se a atualização da flag KV falhar após gravar o marcador, admin reporta erro parcial e orienta repetir pelo mesmo ID; acesso já permanece revogado. A listagem administrativa da flag KV é eventual.

Fontes oficiais consultadas: https://developers.cloudflare.com/kv/concepts/how-kv-works/ e https://developers.cloudflare.com/r2/api/s3/api/ (PUT condicional atômico).

## Interface para o worker de CI

Diretório de trabalho: `infra/cloudflare` (Node 24+).

```text
npm ci
npm run validate-release -- --channel test --version 1.2.3-test.1 --tag v1.2.3-test.1 --candidate candidate.json --pubkey test.pub --base-url https://horus-updates-test.SUBDOMINIO.workers.dev
npm run publish-manifest -- --channel test --version 1.2.3-test.1 --tag v1.2.3-test.1 --candidate candidate.json --pubkey test.pub --base-url https://horus-updates-test.SUBDOMINIO.workers.dev
```

- O primeiro comando de release não grava latest; o segundo repete a validação e publica latest por último.
- `--base-url` é a origem HTTPS, sem caminho, credencial, query ou fragmento; script acrescenta canal/releases/versão/arquivo.
- `release.json`: `version`, `sha256` (64 hex minúsculos do EXE), `size` (bytes inteiros positivos do EXE), `notes`, `pub_date`, `commit` (40 hex). Notas/data iguais ao candidato.
- `.exe.sig` contém a string base64 gerada pelo signer Tauri; a mesma string fica em `platforms.windows-x86_64.signature`. Chave pública: arquivo `.pub` Tauri (base64 externo), também aceita texto público minisign completo.
- Validator/publisher remotos usam S3 SigV4: `CLOUDFLARE_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, somente por env/secrets de CI, credencial limitada ao bucket do canal. Admin KV/R2 usa separadamente Wrangler OAuth preexistente ou `CLOUDFLARE_API_TOKEN`.
- `--directory PASTA` permite validação/publicação simuladas em diretório local com o mesmo layout de chaves.
- A verificação de igualdade da tag com os três manifests do produto continua responsabilidade do CI; estes scripts verificam tag versus versão fornecida. Não alteram/bumpam versões do produto.
- Releases publicadas devem ser imutáveis; CI deve serializar publicação por canal e nunca excluir/sobrescrever artefatos publicados. CAS protege latest contra publicadores concorrentes, não faz transação entre objetos distintos.

## Verificações executadas

- `npm test` em `infra/cloudflare`: **60/60 testes aprovados**, 2 arquivos, Vitest 3.2.7, R2/KV simulados pelo Miniflare; 30 de Worker e 30 de publicação/admin.
- Inclui revogação imediata na requisição seguinte com flag KV ainda ativa; erro R2 nega antes de consultar KV; erro KV nega; canais realmente separados; token ausente/errado/revogado/outro canal; Range/HEAD; ausência de cada artefato; assinatura/hash adulterados; duplicação/regressão; corrida CAS; proteção de emissão contra logs.
- Assinatura real de fixture pública CC0 do projeto minisign-js; nenhuma chave privada foi copiada ou gerada.
- `npm run typecheck`: aprovado.
- Smoke administrativo usando apenas o armazenamento local do Wrangler: emissão com hash, listagem, marcador de revogação R2 e flag KV aprovados; token mantido apenas em memória e omitido da saída. Nova leitura com `CI=true` também aprovada.
- `git diff --cached --check`: aprovado antes do commit; README conferido com 60 linhas; arquivos selecionados e diff revisados.
- Uma falha inicial no adaptador R2 do teste (ETag HTTP entre aspas versus ETag interno da API Worker) foi corrigida no teste; suíte final integralmente aprovada.

## Pendências e limites

Usuário: habilitar/configurar recursos privados/free, autenticar, substituir IDs KV fictícios, fornecer credenciais mínimas por ambiente, decidir custódia/chave de produção e autorizar deploy/publicação. Recursos reais, conta Cloudflare, roteamento workers.dev, quotas e credenciais remotas não foram testados.

Nenhum login/deploy/provisionamento/upload remoto, push/PR/merge, instalador/app Hórus, inferência ou operação paga foi executado. Nenhuma chave de produção foi gerada; nenhuma pasta operacional ou banco foi acessado. O único arquivo fora do escopo versionado é este relatório no caminho explicitamente solicitado.

Travas de rede/CI da aplicação e integração do keyring pertencem aos demais workers; revisão e integração do lote ficam com o Maestro. Instalação real continua dependente de ensaio autorizado em identidade isolada/VM.

## Retorno da revisão independente — 17/11/2026

Escopo desta rodada: somente `infra/cloudflare/admin/tokens.mjs`, seu teste `tests/publication.test.mjs` e este relatório; nenhum contrato, configuração, endpoint ou dependência foi alterado. Novo commit corretivo, sem reescrever o commit anterior.

1. Confirmado no código: `revoke()` gravava marcador antes de conferir o cadastro/canal. Agora valida ID, lê o registro e confere o canal antes de qualquer escrita; ID ausente ou de outro canal é recusado com mensagem clara, sem marcador R2 nem flag KV. A ordem das escritas permanece marcador R2 antes da flag KV. Se o cadastro recém-emitido ainda não estiver visível no KV, a leitura falha sem escrita; repita após a propagação, sem alegar revogação bem-sucedida.
2. Confirmado: o catch anterior mascarava o erro de canal e descartava a causa. Leitura KV e escrita do marcador agora propagam o erro original; falha na gravação da flag preserva `Error.cause` e reporta explicitamente que o acesso já foi revogado no R2 e que a atualização KV deve ser repetida. O CLI mantém a saída sanitizada, sem imprimir causas internas potencialmente sensíveis.
3. A hipótese de truncamento não se confirmou na versão fixada: Wrangler 4.164.0 já percorre os cursores e agrega todas as páginas, tanto localmente (`namespace.list`, `list_complete`, `cursor`) quanto remotamente (`listKVNamespaceKeys` → `fetchListResultBase`, `result_info.cursor`). A CLI não oferece `--cursor`; duplicar essa paginação no wrapper seria incorreto. Esse comportamento foi explicitado junto a `wranglerAdmin().list()` e coberto com teste de integração real da CLI local: semeia 1.001 chaves, comprova que a primeira página de 1.000 está incompleta e possui cursor, confere a recuperação de todos os IDs e limpa somente suas próprias chaves no finally, preservando estado preexistente. A paginação remota foi inspecionada no código instalado, sem acessar a conta.

Validação final: `npm test` **64/64 aprovados** (30 Worker + 34 publicação/admin), incluindo canal errado/ausência sem escrita, propagação de erro, causa da falha parcial e múltiplas páginas reais; `npm run typecheck` e `git diff --cached --check` aprovados. Worktree limpa após o commit; nenhuma ação Cloudflare remota, push, merge, PR ou instalação operacional foi executada. Ressalva de rate limiting em memória permanece documentada, fora do pedido desta rodada.
