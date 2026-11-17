# HOR-164 — Revisão Independente do Plano de Persistência (M1)

**Data:** 17/11/2026  
**Base verificada:** `C:\Users\yannn\01. Projetos\HORUS\horus` (`main` `0a1e4ce`) — inspeção somente leitura.  
**Documento revisado:** `M1-plano-persistencia.md` (+ `M1-rg-sqlite.txt`, `M1-sql-functions.txt`).

---

## VEREDITO: **COM RESSALVAS**

O plano é tecnicamente sólido, reflete fielmente o inventário de SQLite do repositório (21 tabelas ativas, 6 triggers, 30 `BEGIN IMMEDIATE` + 1 `BEGIN EXCLUSIVE` em produção, 3 tabelas opcionais de journal e 4 tabelas históricas `horus_sync`) e propõe uma arquitetura coerente com os fatos verificados da máquina (Psycopg 3 sync, Supavisor sessão `5432`, `sslmode=verify-full` com CA Supabase embutida, senha write-only no cofre do SO).  
Não há bloqueio crítico que invalide o desenho, mas existem **3 achados altos**, **6 médios** e **3 baixos** que o Codex deve ajustar no plano **antes** de congelar os contratos de I1/I2.

---

## Tabela de Achados

| ID | Severidade | Área | Fato × Hipótese | Evidência (`arquivo:linha`) | Achado e Impacto |
|---|---|---|---|---|---|
| A1 | **Alto** | Semântica / Consumo único (`human_checkpoints`) | **Fato** | `human_checkpoints.py:139-163` | O plano resume `consume` como `UPDATE WHERE consumed_at IS NULL AND expires_at>clock_timestamp() RETURNING`. Sem subselect `WHERE id = (SELECT id ... ORDER BY seq DESC LIMIT 1 FOR UPDATE)` e sem filtro/validação explícita de `limit_amount`/`currency` antes do `UPDATE`, um `UPDATE` direto por `(action, subject)` pode consumir **múltiplos** checkpoints abertos ou perder a distinção de recusa (`checkpoint_missing` vs `checkpoint_consumed` vs `checkpoint_expired` vs `checkpoint_limit_exceeded`). Além disso, `clock_timestamp()` no SQL diverge de `self.clock()` injetado em testes. |
| A2 | **Alto** | Conexão / Rotação e falha do cofre | **Fato** | `M1-plano-persistencia.md:155,161-171` | O plano define 7 estados de conexão, mas: (1) não prevê estado/código distinto quando o **cofre do SO (keyring)** falha ou está indisponível (retornar `not_configured` induziria o usuário a redigitar senha sem conseguir salvar); (2) não especifica **invalidação/recriação do `ConnectionPool`** quando a senha do `horus_app` é rotacionada via `POST` write-only com o backend já em execução. |
| A3 | **Alto** | Semântica / Orçamento global (`security.py`) | **Fato** | `security.py:358-441`; `workflow_hermes.py:241-258` | `budget_usage`, `daily_tokens` e `daily_runs` agregam **todos** os `cognitive_runs` (`provider='hermes' AND cognitive_client='live'`), incluindo `total_budget_limit` global entre dias. O plano cita `pg_advisory_xact_lock por orçamento/bucket`, mas precisa fixar que a chave do lock de orçamento Hermes é **global** (não por workflow ou por dia). Adicionalmente, `security.py:380` faz `row['created_at'][:10] == today` em string ISO — ao migrar `created_at` para `timestamptz`, o slice de string quebra e deve ser convertido para data UTC explícita. |
| A4 | **Médio** | Hash / Idempotência (`jobs.payload` e `productions.script_review`) | **Fato** | `storage.py:75-81`; `production.py:327,347` | `storage.py:create_job` compara `existing["payload"] != encoded` (string JSON ordenada); no schema PG proposto, `jobs.payload` vira `jsonb` **sem** `payload_canonical bytea` — o código precisará comparar `dict` (ou manter canonical), atento a `1` vs `1.0` em JSONB. Em `production.py:327`, `script["review"] != row["script_review"]` compara JSON de `scripts.review` (que tem `canonical` no plano) com `productions.script_review` (só `jsonb` no plano); e `production.py:347` grava `plan` direto em disco via `write_text(plan)`, exigindo serialização explícita de `jsonb`→JSON string. |
| A5 | **Médio** | Incrementos / Dependência oculta (`retention.py` em I6) | **Fato** | `retention.py:90-134`; `M1-plano-persistencia.md:259,266` | O plano aloca `retention.py` em **I6 (Radar)** e coloca `I3/I4/I6` como paralelos/independentes após I2. Porém, `retention.py` lê e altera `workflows.payload`, `artifacts.content`, `cognitive_runs.request`, `job_attempts.error`, `workflow_events.data`, `scripts.document` e `script_receipts.response` (incluindo `response = encode(...)` e preservação de `content_hash`/`request_hash`). I6 depende dos contratos finais de colunas `jsonb`/`canonical` de I4 e I5. |
| A6 | **Médio** | Migração de dados (`migrate_sqlite_to_pg.py`) | **Fato** | `M1-plano-persistencia.md:120-130,205-217` | O passo 7 prevê *"COMMIT ambíguo → consultar `run_id` antes de repetir"*, mas a tabela de ledger dessa execução (ex.: `horus.migration_runs(run_id, source_sha256, applied_at)`) **não consta** na lista de tabelas adicionais da seção 4. Além disso, o passo 0–3 precisa receber uma **cópia da pasta `data_dir` completa** (não só o arquivo `.sqlite3`), para abranger `radar/radar-youtube.sqlite3` e os arquivos JSON operacionais (`production-comments/*.json`, `deletion-audit.json`, etc.). |
| A7 | **Médio** | Segurança / Egress TCP (`egress.py` vs `libpq`) | **Fato** | `egress.py:1-15`; `M1-plano-persistencia.md:158` | O plano reconhece corretamente que `EgressGateway` só cobre HTTP e não `libpq`/TCP 5432, mas não detalha o controle substituto: `libpq` resolve DNS internamente (sem a proteção contra DNS rebinding / IP privado/loopback de `egress.py` quando fora de testes locais). É recomendável validar o endpoint/IP resolvido na camada `database/` (fixando `hostaddr` junto com `host` para manter `verify-full` SNI). |
| A8 | **Médio** | Risco operacional / Rede corporativa e keepalives | **Hipótese** (risco de infraestrutura) | `M1-plano-persistencia.md:147-157` | Redes corporativas e o Supavisor derrubam conexões TCP ociosas silenciosamente. O plano não especifica parâmetros de `keepalives` (`keepalives=1`, `keepalives_idle`, `keepalives_interval`, `keepalives_count`) nem `check` / `max_idle` no `psycopg_pool.ConnectionPool`, aumentando chance de erros transitórios no primeiro uso após ociosidade. |
| A9 | **Médio** | Risco operacional / Lease durante queda de internet | **Hipótese** (risco de concorrência) | `M1-plano-persistencia.md:184,195-197` | O plano permite que um job local já iniciado termine em *staging* se a internet cair e seja reconciliado depois. Porém, se a queda durar mais que o `lease_until` do job/worker, o worker não conseguirá renovar o lease no PG; na reconexão, o próprio restart ou outro nó pode marcar o job como `interrupted` antes da reconciliação. Falta definir a regra de reconciliação de *staging* quando o lease expirou por queda de rede sem outro executor ativo. |
| A10 | **Baixo** | Completude do mapa / Módulos JSON na seção 2 | **Fato** | `review_comments.py`, `project_import.py`, `update_backup.py` | Embora citados nas seções 3, 4 e 9 (I7/I8), `review_comments.py` e `project_import.py` ficaram ausentes da tabela da seção 2 ("Mapa real por módulo"), onde `notifications.py` (também baseado em JSON+leitura) foi incluído. |
| A11 | **Baixo** | Gate "nenhum SQLite escondido" | **Fato** | `M1-plano-persistencia.md:267`; `radar_youtube/state.py:162` | A lista de busca do gate (`sqlite/PRAGMA/last_insert_rowid/rowid/INSERT OR/sidecars`) omitiu `json_extract`, `julianday` e `executescript`, e o bloqueio de `sqlite3` em runtime via `sys.modules` precisa cobrir também os **subprocessos workers** (`production_worker.py`, `narration/*_worker.py`, `radar_youtube/__main__.py`). |
| A12 | **Baixo** | Risco operacional / Plano Free (`pg_dump` e pausa) | **Fato** | `M1-plano-persistencia.md:240,276` | No plano Free, um projeto pausado por inatividade **não** acorda via conexão TCP na porta 5432 (dá timeout/erro); o diagnóstico `unreachable` deve orientar o usuário a verificar pausa no painel Supabase. Além disso, o plano exige `pg_dump` regular externo ao app, mas não define de onde o usuário final obterá o binário `pg_dump` major-compatível no Windows sem Docker/admin. |

---

## Verificação dos 7 Pontos Solicitados

1. **Completude do mapa:**
   - **Arquivos/módulos:** Os 8 arquivos que importam `sqlite3` (`api.py`, `backups.py`, `data_deletion.py`, `local_data_export.py`, `migrations.py`, `persistence_contracts.py`, `radar_youtube/state.py`, `storage.py`), mais `worker_lock.py`, os scripts (`ensaio_atualizacao_banco.py`, `check-installer-data.py`, `test-installer-data-isolation.ps1`, `updater-e2e*`, `smoke-windows-package.py`, `measure-local-pilot.py`, `prepare-first-video.py`, `vm/guest*.ps1`), `backend/measure_checkpoints.py` e `frontend/src-tauri/src/updater.rs` foram conferidos e batem com o repositório. Apenas os módulos JSON `review_comments.py` e `project_import.py` faltaram na tabela da seção 2 (mas estão na seção 9).
   - **Schema (21 tabelas, índices, triggers, CHECKs v7):** Todas as 17 tabelas de `migrations.py` (v1–v12) + 4 tabelas de `radar_youtube/state.py` estão presentes no schema PG proposto, com os 6 triggers (`license_reviews_no_update/delete`, `transmissions_no_update/delete`, `human_checkpoints_no_delete/consume_once`), índices e o `CHECK` v7 (`'simulated','local_demo','local_pilot'`) preservados. As 3 tabelas `sync_*` e as 4 de `horus_sync` estão corretamente tratadas.

2. **Semântica:**
   - A contagem de **10** `BEGIN IMMEDIATE` em `workflows.py` (linhas 252, 298, 326, 401, 456, 528, 548, 575, 615, 637) é exata; no total são **30** `BEGIN IMMEDIATE` e **1** `BEGIN EXCLUSIVE` (`radar_youtube/state.py:77`) no código de produção.
   - Pontos de atenção semântica registrados em **A1** (`human_checkpoints.consume`), **A3** (`security.py` lock global e `created_at[:10]`) e **A4** (`jobs.payload` e `productions.script_review`).

3. **Hash / Idempotência:**
   - A estratégia de manter `*_canonical bytea` em `artifacts.content`, `approvals.request`, `authorizations.request`, `scripts.document/review`, `script_receipts.request/response`, `production_receipts.request`, `license_reviews.request`, `human_checkpoints.request` e `deletion_events` está correta e protege contra reordenação/formatação de `jsonb`.
   - Campos sem `canonical` verificados: `cognitive_runs.request` (`request_hash` é gravado no insert e preservado na redação em `retention.py:118`), `job_attempts.input_artifacts` (`input_hash` vem do `content_hash` do source). Pendência apenas em `jobs.payload` (comparado por string em `storage.py:80`) e `productions.script_review` (comparado com `scripts.review` em `production.py:327`) — ver **A4**.

4. **Conexão / Segurança:**
   - Psycopg 3 sync + `psycopg_pool` + Supavisor sessão `5432` + `verify-full` + CA Supabase embutida + senha write-only no cofre é **100% coerente** com os fatos verificados e trata explicitamente a superação de `docs/seguranca-persistencia-remota.md`.
   - Lacunas registradas em **A2** (estado para falha de cofre e recriação do pool na rotação de senha) e **A7** (validação de IP/DNS para `libpq` fora do `EgressGateway` HTTP).

5. **Incrementos (I0…I-final):**
   - Divisão de arquivos entre I0–I8, I-mig, I-UI e I-final é exclusiva e sem conflito com HOR-163.
   - Dependência a ajustar em **A5** (`retention.py` em I6 depende de I4/I5) e reforço do gate em **A11** (`json_extract`/`julianday`/`executescript` + subprocessos).

6. **Migração de dados (passos 0–8):**
   - Ordem de carga, `DEFERRABLE` para ciclo `workflows↔artifacts`, ordenação topológica de `jobs.depends_on` e `license_reviews.supersedes`, `OVERRIDING SYSTEM VALUE` + `setval` e conferência por hash cobrem bem perda de dados e falhas.
   - Ajustes necessários em **A6** (tabela explícita de ledger `migration_runs` para `run_id`, e entrada como cópia completa de `data_dir` para incluir Radar e JSONs).

7. **Riscos omitidos:**
   - Registrados em **A8** (keepalives TCP / `check` no pool contra corte silencioso em rede corporativa), **A9** (expiração de lease durante queda de internet em job local) e **A12** (projeto Free pausado não acorda via 5432 + origem do binário `pg_dump` para o usuário).

---

## Lista do que o Codex deve corrigir no plano ANTES dos incrementos I1/I2

1. **[A1]** Detalhar o SQL de `human_checkpoints.consume` em PG com seleção atômica do último checkpoint elegível (`ORDER BY seq DESC LIMIT 1 FOR UPDATE`), preservação das causas de `CheckpointRefused` e tratamento explícito do relógio de teste (`clock`).
2. **[A2]** Adicionar estado/código específico para **falha/indisponibilidade do cofre (keyring)** e especificar a **drenagem/recriação do `ConnectionPool`** quando a credencial for atualizada/rotacionada via fluxo write-only.
3. **[A3]** Explicitar que o `pg_advisory_xact_lock` do orçamento Hermes (`security.py`) usa chave **global** (não por workflow/dia) e registrar a conversão do filtro `created_at[:10]` para comparação UTC em `timestamptz`.
4. **[A4]** Definir como `storage.py:create_job` e `production.py:327,347` tratam `jobs.payload` e `productions.script_review`/`plan` ao virar `jsonb` (comparação estrutural vs `*_canonical`).
5. **[A5]** Ajustar a ordem/dependência de `retention.py` (I6) para depender dos contratos de schema/canonical de I4 e I5 (ou mover `retention.py` para após I4/I5).
6. **[A6]** Incluir `horus.migration_runs` (para suportar consulta de `run_id` em `COMMIT` ambíguo) na seção 4 e exigir cópia do `data_dir` completo (não só `.sqlite3`) no passo 0 de `migrate_sqlite_to_pg.py`.
7. **[A7 / A8 / A9 / A11 / A12]** Documentar: validação DNS/IP no `database/` para `libpq` (`host`+`hostaddr`), `keepalives` e `check` no `ConnectionPool`, regra de reconciliação de *staging* se o lease expirar durante queda de rede, inclusão de `json_extract`/`julianday`/`executescript` e subprocessos no gate anti-SQLite, e orientação de pausa Free / fonte do `pg_dump` para backups.
