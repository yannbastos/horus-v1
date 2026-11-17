# HOR-163 (E) — Pré-requisitos Cloudflare e Ações do Usuário

Pesquisa realizada exclusivamente na documentação oficial da Cloudflare (`developers.cloudflare.com`, consultada em 2026-11-17). Nenhuma alteração foi feita no repositório ou na conta Cloudflare.

---

## 1. R2, Workers, KV e Faturamento

### R2 — Ativação, franquia gratuita e cobrança
- **Método de pagamento na ativação:** **Sim.** Para ativar o R2 é necessário concluir o fluxo de checkout no dashboard (`Storage & databases > R2 > Overview`, *"Complete the checkout flow to add an R2 subscription to your account"*) ([R2 Get Started](https://developers.cloudflare.com/r2/get-started/), atualizada em 2026-11-12). A política da Cloudflare exige método de pagamento válido antes de habilitar assinaturas (cartões Visa, Mastercard, Amex, Discover, UnionPay, PayPal, Apple Pay, Google Pay ou Stripe Link; pré-pagos/gift cards podem ser recusados) e prevê pré-autorização temporária para serviços *usage-based* como o R2 ([Billing Policy](https://developers.cloudflare.com/billing/understand/billing-policy/), atualizada em 2026-05-29).
- **Franquia gratuita mensal (apenas Standard Storage):**
  - **Armazenamento:** `10 GB-mês / mês` (excedente: `$0,015 / GB-mês`).
  - **Operações Classe A** (mutação: `PutObject`, `ListObjects`, `CreateMultipartUpload`, etc.): `1 milhão / mês` (excedente: `$4,50 / milhão`).
  - **Operações Classe B** (leitura: `GetObject`, `HeadObject`, etc.): `10 milhões / mês` (excedente: `$0,36 / milhão`).
  - **Operações gratuitas:** `DeleteObject`, `DeleteBucket`, `AbortMultipartUpload`.
  - **Saída (egress para a Internet):** **Gratuita** via Workers API, S3 API e `r2.dev` ([R2 Pricing](https://developers.cloudflare.com/r2/pricing/), atualizada em 2026-10-30).
- **Ao exceder e controles de gasto:**
  - O R2 **não tem hard cap** (bloqueio automático): excedentes são faturados em *pay-as-you-go* com arredondamento para cima na próxima unidade inteira.
  - O **Spend Management** (bloqueio automático de operações) cobre **apenas Workers e Durable Objects**, **não** inclui R2 nem KV ([Spending limits and alerts](https://developers.cloudflare.com/billing/manage/spend-management/), atualizada em 2026-11-13).
  - Para o R2 existem apenas **Budget alerts** (`Manage Account > Billing > Billable Usage > Create budget alert`, envio de e-mail ao cruzar valor em USD **sem pausar o serviço**) e **Usage notifications** por produto ([Budget alerts](https://developers.cloudflare.com/billing/manage/budget-alerts/), atualizada em 2026-11-13).

### Workers e KV (Plano Free)
- **Workers Free:** `100.000 requisições/dia` (reset 00:00 UTC), `10 ms` de CPU time por invocação, `128 MB` de memória por isolate, sem cobrança de egress ([Workers Pricing](https://developers.cloudflare.com/workers/platform/pricing/), atualizada em 2026-11-13; [Workers Limits](https://developers.cloudflare.com/workers/platform/limits/)). Ao exceder `100k req/dia` no Free, as requisições falham com erro (sem cobrança).
- **Workers KV Free:** `100.000 leituras/dia`, `1.000 gravações/dia`, `1.000 deleções/dia`, `1.000 list/dia`, `1 GB` armazenado, até `1.000` namespaces (reset 00:00 UTC). Ao exceder qualquer limite diário no plano Free, a operação falha com erro — **sem risco de cobrança** ([KV Pricing](https://developers.cloudflare.com/kv/platform/pricing/) e [KV Limits](https://developers.cloudflare.com/kv/platform/limits/), atualizadas em 2026-10-30).

### Conclusão: 100% gratuito SEM cartão?
- **Workers + KV:** **Sim**, operam no plano Free sem cartão e com corte rígido ao atingir a cota diária.
- **R2:** **Não sem método de pagamento**, pois o checkout da assinatura exige forma de pagamento cadastrada (ex.: cartão ou PayPal) e não possui teto de bloqueio automático.
- **Alternativa gratuita sem cartão (se o usuário optar por não cadastrar pagamento no R2):** usar **GitHub Releases** como origem dos binários/manifestos + **Cloudflare Worker (Free) + KV (Free)** na frente autenticando o `Authorization: Bearer <token>`.
  - *O que permanece igual no contrato:* URL `https://<worker>.<subdominio>.workers.dev`, rotas `/<channel>/latest.json` e `/<channel>/releases/<version>/<arquivo>`, token por instalação (SHA-256 no KV) e segredo no cofre do Windows.
  - *O que muda:* o CI publica os artefatos em GitHub Releases em vez de enviar ao R2; o Worker faz `fetch()` para a Release (com secret `GITHUB_TOKEN` read-only se o repositório for privado, seguindo redirects para o CDN de assets do GitHub) e faz *streaming* da resposta para o cliente.

---

## 2. Autenticação (`wrangler` e API Tokens de Menor Privilégio)

### Fluxo local (`wrangler login` e `wrangler whoami`)
- `npx wrangler login`: abre o navegador para login OAuth na Cloudflare e recebe o callback em `http://localhost:8976` (alternativas: `--browser=false` ou `--device`). Por padrão solicita **todos os escopos disponíveis**; recomenda-se `--use-keyring` para gravar credenciais no cofre do SO em vez de arquivo TOML em texto puro, ou restringir escopos com `--scopes` (lista via `npx wrangler login --scopes-list`) ([Wrangler General Commands](https://developers.cloudflare.com/workers/wrangler/commands/general/), atualizada em 2026-09-02).
- `npx wrangler whoami`: exibe usuário, contas associadas (ID/nome) e permissões do token ativo.

### API Tokens de menor privilégio para o CI
Não usar `wrangler login` nem Global API Key no CI. Criar **dois tokens separados**:

1. **Token 1 — Deploy do Worker e KV (`CLOUDFLARE_API_TOKEN` no CI):**
   - Dashboard → **Manage Account > API Tokens** (preferir *Account API Token*, de serviço) → **Create Token** → **Create Custom Token** ([Create API token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/), atualizada em 2026-11-06).
   - Permissões exatas ([API Permissions](https://developers.cloudflare.com/fundamentals/api/reference/permissions/)):
     - `Account` | `Workers Scripts` | `Edit`
     - `Account` | `Workers KV Storage` | `Edit` (ou `Read` se o CI não manipular chaves KV)
     - `Account` | `Account Settings` | `Read`
   - *Account Resources:* selecionar apenas a conta alvo. Configurar **Token expiration** e, se possível, filtro de IP. Guardar `CLOUDFLARE_API_TOKEN` e `CLOUDFLARE_ACCOUNT_ID` apenas nos secrets do CI.

2. **Token 2 — Upload de releases no R2 por bucket (se R2 for ativado):**
   - Dashboard → **Storage & databases > R2 > Overview** → **Manage** (em *API Tokens*) → **Create Account API token** ([R2 Authentication](https://developers.cloudflare.com/r2/api/tokens/), atualizada em 2026-10-01).
   - Permissão: **Object Read & Write** (*não* usar *Admin Read & Write*).
   - Escopo: **Apply to specific buckets only** → selecionar apenas o bucket do canal (`horus-updates-test` ou `horus-updates-prod`; idealmente um token por canal).
   - Copiar `Access Key ID` e `Secret Access Key` (exibidos **uma única vez**) direto para os secrets do CI. Endpoint S3: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`. *(Nota: `Object Read & Write` vale para a API S3, não para a Cloudflare REST API.)*

---

## 3. Subdomínio `workers.dev`

- **Registro:** cada conta possui um subdomínio global e público no formato `<SUBDOMINIO>.workers.dev`, configurado em **Workers & Pages > Overview > Change** ao lado de **Your subdomain** ([workers.dev](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/), atualizada em 2026-11-06).
- **Ativação por Worker:** habilitado por padrão no deploy (`<WORKER_NAME>.<SUBDOMINIO>.workers.dev`). Pode ser desativado em **Settings > Domains & Routes > Disable** ou via `workers_dev = false` no arquivo do Wrangler (se desativar só no dashboard, o próximo `wrangler deploy` reativa).
- **Privacidade e armadilhas:**
  - A rota `workers.dev` é **pública na Internet**; a segurança depende exclusivamente da validação do `Authorization: Bearer <token>` no Worker.
  - **Browser Integrity Check (BIC):** a zona `workers.dev` aplica BIC obrigatório e **não desativável**. Clientes com `User-Agent` padrão de biblioteca (ex.: Python `urllib`) recebem **erro 1010** antes do Worker executar. O cliente Tauri/Rust e os scripts de teste **devem enviar um `User-Agent` descritivo** (ex.: `Horus-Updater/1.0`).
- **Nomes sugeridos:** `horus-updates-test` e `horus-updates-prod` (dois Workers separados garantem isolamento de bindings R2/KV entre `test` e `production`; ambos atendem ao limite de ≤ 63 chars, `[a-z0-9-]`, sem hífen nas pontas). Caso use Worker único, `horus-updates`.

---

## 4. Pontos de Risco (Instalador ~100–300 MB)

- **Download via Worker (Streaming obrigatório):** não há limite rígido de tamanho de *response body* no Workers, e o *wall time* HTTP é ilimitado enquanto o cliente consome o stream ([Workers Limits](https://developers.cloudflare.com/workers/platform/limits/)). Porém, o isolate tem **128 MB de memória** e **10 ms de CPU time** (Free). O Worker **nunca** deve bufferizar o instalador em memória (`arrayBuffer()`/`bytes()` falhará com `Memory limit would be exceeded before EOF`); deve retornar `new Response(object.body, ...)` em *streaming* direto.
- **Upload NUNCA via Worker:** o limite de *request body* entrando em um Worker no plano Free é **100 MB** (`413 Request entity too large`). Uploads de instaladores (100–300 MB) pelo CI devem ir direto para o R2 via **API S3** (suporta até `5 GiB` *single-part* e `4,995 TiB` *multipart*) ([R2 Limits](https://developers.cloudflare.com/r2/platform/limits/), atualizada em 2026-10-30), ou direto para GitHub Releases.
- **Cache CDN:** o limite de cache no plano Free é `512 MB`, mas respostas autenticadas por Bearer devem usar `Cache-Control: private, no-store` para evitar cache compartilhado entre tokens.
- **HTTPS/TLS:** `*.workers.dev` fornece TLS/HTTPS válido por padrão, atendendo ao requisito de HTTPS obrigatório sem domínio próprio.
- **Requisições `Range`:** a Workers API do R2 (`bucket.get(key, { range: request.headers })`) suporta `Range` nativamente ([R2 Workers API Reference](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/), atualizada em 2026-11-04). O Worker deve encaminhar o cabeçalho `Range` e responder `206 Partial Content` + `Content-Range` + `Accept-Ranges: bytes` para permitir retomada de download.

---

## 5. Checklist Final para o Usuário

*(Nunca envie senhas, API tokens, Secret Access Keys ou chaves privadas no chat — apenas confirmações e dados públicos abaixo.)*

1. **Decidir armazenamento (R2 vs GitHub Releases):** escolher se cadastrará método de pagamento na Cloudflare para ativar o R2 (com alerta de orçamento) ou se prefere a alternativa gratuita sem cartão via GitHub Releases + Worker. **Devolver:** `"decisão: R2"` ou `"decisão: GitHub Releases"`.
2. **Criar/acessar conta Cloudflare** em [dash.cloudflare.com](https://dash.cloudflare.com/). **Devolver:** `"feito"`.
3. **Escolher e registrar o subdomínio `workers.dev`:** em **Workers & Pages > Overview > Your subdomain > Change**, definir o subdomínio da conta. **Devolver:** o subdomínio escolhido (ex.: `meu-subdominio.workers.dev`).
4. **Autenticar Wrangler localmente (com cofre do SO):** rodar `npx wrangler login --use-keyring` e concluir o OAuth no navegador. **Devolver:** `"feito"`.
5. **Validar sessão local:** rodar `npx wrangler whoami`. **Devolver:** a saída do comando (nome da conta/permissões; se preferir, oculte o e-mail — o `Account ID` pode ser informado apenas se necessário para configuração).
6. **(Se escolheu R2) Ativar assinatura R2:** em **Storage & databases > R2 > Overview**, concluir o checkout gratuito do R2. **Devolver:** `"feito"`.
7. **(Se escolheu R2) Criar alerta de orçamento:** em **Manage Account > Billing > Billable Usage > Create budget alert**, definir um teto em USD (ex.: `$1.00`) para receber e-mail caso a franquia gratuita seja ultrapassada. **Devolver:** `"feito"`.
8. **(Se escolheu R2) Criar buckets separados:** criar `horus-updates-test` e `horus-updates-prod` em R2 (não habilitar `r2.dev` público). **Devolver:** `"feito"`.
9. **Criar API Token do CI para Worker + KV:** em **Manage Account > API Tokens > Create Custom Token**, conceder apenas `Workers Scripts: Edit`, `Workers KV Storage: Edit` e `Account Settings: Read` na sua conta; salvar nos secrets do GitHub (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`). **Devolver:** `"feito"` (nunca o token).
10. **(Se escolheu R2) Criar R2 API Tokens por bucket para o CI:** em **R2 > Overview > Manage API Tokens**, criar token com **Object Read & Write** restrito ao bucket de `test` (e outro para `prod`); salvar `Access Key ID` e `Secret Access Key` nos secrets do GitHub. **Devolver:** `"feito"` (nunca as chaves).
