# HOR-164 (E2) — Supabase: Limites, Backups, Pausa, Conexão e Ações do Usuário

**Data da pesquisa:** 2026-11-17  
**Escopo:** Pesquisa exclusiva em fontes oficiais da Supabase (`supabase.com/docs`, `supabase.com/pricing`, `supabase.com/security`, `supabase.com/legal/dpa`, `supabase.com/sla`), lidas via `Invoke-WebRequest`.  
**Contexto arquitetural:** Persistência 100% em Supabase PostgreSQL (banco operacional único), backend FastAPI local em Windows, frontend Tauri sem credenciais privilegiadas, sem fallback silencioso para SQLite.

---

## 1. Plano Gratuito × Uso como Banco Operacional Único

| Item | Plano Free (Instância Nano) | Plano Pro (Mínimo Pago) |
| :--- | :--- | :--- |
| **Custo mensal** | **$0/mês** (sem cartão de crédito) | **A partir de $25/mês** (inclui $10 de crédito compute = 1 instância Micro) |
| **Tamanho máximo do banco** | **500 MB** por projeto (read-only ao exceder) | **8 GB** de disco incluídos por projeto (+$0,125/GB excedente) |
| **Pausa por inatividade** | **Sim, após 7 dias** de baixa atividade de banco | **Não** (projetos pagos nunca são pausados) |
| **Projetos ativos** | Máximo de **2 projetos ativos** (pausados não contam) | Ilimitados (1º projeto incluído; adicionais a partir de $10/mês) |
| **Conexões diretas (`max_connections`)** | **60** | **60** (Micro) até 500+ (computes maiores) |
| **Conexões no Pooler (Supavisor)** | **200 clientes** (pool size padrão: 15 por role) | **200 clientes** (Micro) até 12.000 |
| **Egress** | **5 GB/mês** (+ 5 GB cached egress) | **250 GB/mês** (+ 250 GB cached; +$0,09/GB excedente) |
| **File Storage** | **1 GB** | **100 GB** (+$0,0213/GB excedente) |
| **Requisições de API** | **Ilimitadas** (sujeito a Fair Use Policy) | **Ilimitadas** (sujeito a Fair Use Policy) |
| **CPU / RAM** | **Shared CPU • 500 MB RAM** (5 MB/s, 250 IOPS baseline) | **Micro: Shared CPU • 1 GB RAM** (11 MB/s, 500 IOPS baseline) |
| **Backups automáticos** | **Não possui** | **Diários (retenção de 7 dias)** |

### Pausa por inatividade (detalhes)
- **Critério e aviso:** Projetos Free com poucas consultas de usuário nos últimos **7 dias** são pausados. O dono recebe um email de aviso ~1 semana antes e um email de confirmação ao pausar.
- **Perda de dados e reativação:** Os dados e configurações **não são apagados** na pausa, mas há uma **janela máxima de 1 ano** para reativar no Dashboard (*Selecionar organização → Projeto → Resume project*). Após 1 ano, backups estáticos podem não ser restauráveis.
- **Impacto no app:** Enquanto pausado, o banco fica inacessível e o app desktop falha ao conectar até que alguém reative manualmente no painel.

### Quando o Free deixa de ser adequado e controle de custos
- **Inadequação imediata para produção única:** O plano Free é adequado apenas para **desenvolvimento/homologação**. Para banco operacional único, a **pausa em 7 dias** e a **ausência de backups automáticos** tornam o plano Free inadequado; além disso, 500 MB de banco ou >5 GB de egress exigem upgrade.
- **Cobrança e Hard Cap:**
  - **Free:** Nunca há cobrança automática (não exige cartão). Exceder cotas gera aviso e bloqueio/modo somente leitura.
  - **Pro ($25/mês):** Possui **Spend Cap** ativado por padrão, bloqueando uso excedente nos itens cobertos (Egress, Disk Size, Storage, MAU, Edge Functions, Logs, Realtime) até o próximo ciclo. Itens opt-in previsíveis (Compute adicional, PITR, add-on IPv4, Custom Domain, IOPS/Throughput extra) **não** são cobertos pelo Spend Cap.

**Fontes:** [Pricing](https://supabase.com/pricing), [Project Pausing](https://supabase.com/docs/guides/platform/pausing), [Compute and Disk](https://supabase.com/docs/guides/platform/compute-and-disk), [Control your costs](https://supabase.com/docs/guides/platform/cost-control), [Billing on Supabase](https://supabase.com/docs/guides/platform/billing-on-supabase) (consultados em 2026-11-17).

---

## 2. Backups e Recuperação

- **Plano Free:** **Sem backups automáticos** e sem PITR. A documentação recomenda exportação manual periódica via `supabase db dump` / `pg_dump`.
- **Planos Pagos (Backups Diários):** Pro retém **7 dias**; Team retém **14 dias**; Enterprise retém **30 dias**. Projetos em Postgres ≥ `15.8.1.079` usam **backups físicos** por padrão (restauráveis pelo Dashboard ou Management API, **não** disponíveis para download direto como arquivo SQL e **não** incluem objetos do Storage API nem senhas de roles customizados — que precisam ser redefinidas após restore).
- **PITR (Point-in-Time Recovery):** Add-on pago disponível apenas em **Pro, Team e Enterprise** (a partir de **~$100/mês** para 7 dias de retenção, ~$200/mês para 14 dias, ~$400/mês para 28 dias; usa WAL-G com RPO de até ~2 minutos).
- **Dump lógico em rede IPv4 + TLS corporativo:**
  - `supabase db dump` executa `pg_dump` dentro de um container **Docker** — como esta máquina não tem Docker, **não** funciona localmente.
  - **Solução sem Docker:** Usar o binário cliente **`pg_dump`** diretamente contra o **Shared Pooler em modo Sessão (porta 5432)** (nunca na porta 6543/Transaction, pois `pg_dump` exige sessão persistente).
  - Como o TLS da porta 5432 do pooler **não** é interceptado pelo proxy corporativo, passar `sslmode=verify-full` e `sslrootcert=<caminho_certificado_raiz_supabase>`.
- **Implicação para "backup verificável antes de atualizar":**
  - No Free, qualquer backup antes de atualizar depende 100% de dump lógico gerado pelo próprio fluxo/operador.
  - Mesmo no Pro, backups físicos diários não são baixáveis para verificação local em SQLite/Postgres antes de um update; portanto, para ter um **backup lógico verificável local antes de atualizar**, o recomendável é executar `pg_dump` (via porta 5432 Session mode) ou exportação lógica controlada pelo backend antes de migrações.

**Fontes:** [Database Backups](https://supabase.com/docs/guides/platform/backups), [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres), [Supabase CLI db dump](https://supabase.com/docs/reference/cli/supabase-db-dump) (consultados em 2026-11-17).

---

## 3. Conexão a partir de um App Desktop Windows (FastAPI)

- **IPv4 × IPv6:**
  - Conexão direta (`db.<PROJECT_REF>.supabase.co:5432`) é **somente IPv6** por padrão.
  - O **Shared Pooler (Supavisor)** é **IPv4-only em todos os planos** (Free e pagos).
  - O add-on **Dedicated IPv4** custa **$4/mês** ($0,0055/h), exige plano **Pro+** e troca o registro DNS AAAA por A no endpoint direto.
- **Modos do Supavisor (Shared Pooler):**
  - **Session mode (`aws-<INDEX>-<REGION>.pooler.supabase.com:5432`):** Recomendado para o backend FastAPI em rede IPv4. Mantém estado de sessão, suporta **prepared statements**, `LISTEN`/`NOTIFY`, cursors e `pg_dump`.
  - **Transaction mode (`aws-<INDEX>-<REGION>.pooler.supabase.com:6543`):** Indicado para serverless/edge. **Não suporta** prepared statements (exige `prepare=False` / `statement_cache_size=0` / `pgbouncer=true` no driver), nem query pipelining ou estado de sessão.
- **Formato do usuário e host:**
  - No pooler compartilhado, o usuário deve incluir o ref do projeto: `postgres.<PROJECT_REF>` (ou `<CUSTOM_ROLE>.<PROJECT_REF>`).
  - O `<INDEX>` do host (`aws-<INDEX>-<REGION>.pooler.supabase.com`) varia por projeto e deve ser copiado do botão **Connect** no Dashboard.
- **SSL (`verify-full`):**
  - Baixar o certificado raiz no Dashboard em **Database Settings → SSL Configuration**.
  - Configurar na string/driver `sslmode=verify-full` e `sslrootcert=<caminho_do_certificado>` (usar apenas `sslmode=require` criptografa, mas **não** previne MITM). Opcionalmente ativar *"Enforce SSL on incoming connections"* no Dashboard.
- **Limites de conexões simultâneas:**
  - **Nano (Free)** e **Micro (Pro):** 60 diretas / 200 clientes no pooler (pool size padrão = 15 por role).
  - **Small:** 90 / 400; **Medium:** 120 / 600; **Large:** 160 / 800.
  - *Nota:* o `pool_size` do Supavisor é **por role** conectado.
- **Menor privilégio (Role dedicado, GRANTs e RLS):**
  - **Não** usar `postgres` nem `service_role` no frontend Tauri.
  - No FastAPI, criar um **role dedicado** com login e sem superusuário (`CREATE ROLE horus_app WITH LOGIN PASSWORD '...' NOINHERIT;`), concedendo apenas `USAGE` no schema e `SELECT, INSERT, UPDATE, DELETE` nas tabelas/sequences estritamente necessárias (sem `BYPASSRLS`/`CREATEROLE`/`CREATEDB`).
  - Se a Data API (PostgREST) permanecer ativa, habilitar **RLS** em todas as tabelas (`ENABLE ROW LEVEL SECURITY`) sem políticas permissivas para `anon`/`authenticated`, bloqueando acesso direto via API pública.

**Fontes:** [Connect to your database](https://supabase.com/docs/guides/database/connecting-to-postgres), [Connection management](https://supabase.com/docs/guides/database/connection-management), [Dedicated IPv4 Address](https://supabase.com/docs/guides/platform/ipv4-address), [Postgres SSL Enforcement](https://supabase.com/docs/guides/platform/ssl-enforcement), [Postgres Roles](https://supabase.com/docs/guides/database/postgres/roles) (consultados em 2026-11-17).

---

## 4. Autenticação do CLI e Criação do Projeto de Desenvolvimento

- **Fluxo oficial de autenticação CLI:**
  - **Navegador (interativo):** `npx supabase login` abre o navegador para autorizar e grava o token localmente.
  - **Token (`SUPABASE_ACCESS_TOKEN`):** Gerar um Personal Access Token (`sbp_...`) em `https://supabase.com/dashboard/account/tokens` (com escopo mínimo por organização/projeto) e exportar `SUPABASE_ACCESS_TOKEN` no ambiente (ou rodar `npx supabase login --token <token>`). Em rede com proxy TLS corporativo, comandos HTTPS da CLI precisam da CA corporativa confiável (`NODE_EXTRA_CA_CERTS`).
- **Região recomendada (Brasil / Santa Catarina):**
  - **South America (São Paulo) — `sa-east-1`** (selecionar explicitamente nas *Specific regions* ao criar o projeto, evitando "General regions" como Americas que alocam em `us-east-1`).

---

## 5. Riscos Específicos

1. **Latência por consulta:** Cada query percorre a internet até `sa-east-1` (dezenas de ms de RTT por round-trip, contra <1 ms do SQLite local). Exige evitar queries *N+1* no FastAPI, agrupar operações em transações e manter pool persistente em Session mode (5432) para amortizar o handshake TCP+TLS+SCRAM.
2. **Indisponibilidade e manutenção:** Planos Free e Pro **não possuem SLA contratual de uptime** (99,9% de SLA existe apenas no plano **Enterprise**). Quedas de internet no desktop ou janelas de manutenção/upgrade (<2 min) interrompem o acesso ao banco — o app deve exibir erro claro, sem fallback silencioso.
3. **Conteúdo privado em nuvem de terceiros:**
   - **Localização e criptografia:** Dados ficam hospedados na AWS na região escolhida (`sa-east-1`, São Paulo), criptografados em repouso (AES-256) e em trânsito (TLS).
   - **Retenção:** Dados permanecem até exclusão do projeto (pausados por até 1 ano; exclusão apaga banco e backups no S3 irreversivelmente).
   - **DPA e conformidade:** A Supabase disponibiliza **DPA** (`https://supabase.com/legal/dpa`) com Cláusulas Contratuais Padrão (SCCs) e lista de subprocessadores, atuando como operadora sob instruções do controlador, com certificações **SOC 2 Type 2** e **ISO 27001**.

**Fontes:** [Available regions](https://supabase.com/docs/guides/platform/regions), [Security at Supabase](https://supabase.com/security), [DPA](https://supabase.com/legal/dpa), [SLA](https://supabase.com/sla), [Supabase Login CLI](https://supabase.com/docs/reference/cli/supabase-login) (consultados em 2026-11-17).

---

## 6. Checklist Final para o Usuário (Desenvolvimento)

1. Acessar `https://supabase.com/dashboard` e entrar na sua conta (plano Free não exige cartão).
2. Criar uma **Organização** no plano **Free** (para desenvolvimento).
3. Criar um **Projeto** dentro dessa organização selecionando a região específica **South America (São Paulo) (`sa-east-1`)**.
4. Definir uma **senha forte** para o banco `postgres` e salvá-la apenas no seu cofre/gerenciador de senhas local (**nunca** enviar no chat ou commitar).
5. Aguardar o provisionamento do projeto e abrir **Connect** (topo do painel) → aba **Session pooler** para confirmar o host `aws-<INDEX>-sa-east-1.pooler.supabase.com:5432`.
6. Ir em **Project Settings → Database → SSL Configuration** e baixar o certificado raiz SSL da Supabase.
7. Se for usar a CLI nesta máquina, abrir um terminal local e executar `npx supabase login` autenticando pelo navegador.
8. **O que me devolver aqui:** apenas **"feito"**, o **nome/ref do projeto** e a **região** selecionada (sem senhas, chaves ou tokens).
