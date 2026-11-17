# Revisão Independente — Ferramenta de Migração SQLite → PostgreSQL (`HOR-164 / I-mig`)

- **Escopo auditado:** branch `yannbastos/sb-i-mig`, commit `2e1da6f` (`C:\Users\yannn\orca\workspaces\horus\sb-i-mig`)
- **Artefatos revisados:** `backend/tools/migrate_sqlite_to_pg.py`, `backend/tools/migrate_sqlite_pg/*`, `docs/procedimento-migracao-sqlite-postgres.md`, `backend/tests/pg/migration_fixtures/synthetic_builder.py`, `backend/tests/pg/test_migration_sqlite_to_pg.py`, `backend/src/horus_backend/migrations.py`, `supabase/migrations/20261117160100_horus_tables.sql`, `20261117160200_horus_security.sql`, `20261117160300_horus_coordination.sql`
- **Referências:** `M1-plano-persistencia.md` (§7), `M1-plano-v2-emendas.md` (A4, A6), `I-mig-ferramenta.md`
- **Postura:** somente leitura no repositório, 100% sintético/descartável em loopback (`Cluster` PG local), sem acesso a dados reais ou Supabase remoto.

---

## Veredito: **COM RESSALVAS**

A arquitetura central de migração (mapeamento explícito das 21 tabelas v9–v12, preservação byte a byte de `*_canonical`, `OVERRIDING SYSTEM VALUE` + `setval`, carga e verificação na mesma transação com rollback total, ausência de vazamento de conteúdo privado e isolamento de runtime) está **sólida e verificável**.  
Entretanto, há **1 achado alto** (bypass de guarda operacional via caminho `\\?\` e falta de guarda em `--backup-dir`/`--report-out`) e **2 achados médios** (validação incompleta de NUL/constraints no `dry-run` e lacunas no procedimento de corte real) que **devem ser corrigidos antes de qualquer uso com dados reais**.

---

## 1. Tabela de Achados (Adversarial)

| ID | Sev. | Arquivo:Linha | Tipo | Achado e Evidência |
|---|---|---|---|---|
| **M-01** | **Alto** | `backend/tools/migrate_sqlite_pg/guards.py:16-23,49-63` | **Fato** | **Bypass da guarda operacional via prefixo Windows `\\?\`:** `_norm_key()` chama `path.resolve()`, que preserva `\\?\` em caminhos estendidos, enquanto `_operational_roots()` gera caminhos sem `\\?\`. Em ensaio direto com `validate_source_path(r"\\?\C:\Users\yannn\AppData\Local\Horus\horus.sqlite3", must_exist=False)`, a função **ACEITOU** o caminho operacional sem levantar `MigrationGuardError` (caminhos normais, uppercase e com `..` foram recusados). |
| **M-02** | **Alto** | `backend/tools/migrate_sqlite_pg/runner.py:58-61`; `backend/tools/migrate_sqlite_to_pg.py:76-81` | **Fato** | **`--backup-dir` e `--report-out` não passam por guardas:** nem `backup_dir` nem `report_out` chamam `validate_source_path` / `validate_no_links` / checagem de `_operational_roots()`. Um operador ou script pode gravar `horus.immutable-*.sqlite3` ou relatórios JSON dentro de `%LOCALAPPDATA%\Horus` ou através de junction/symlink. |
| **M-03** | **Médio** | `backend/tools/migrate_sqlite_pg/sqlite_reader.py:102-149`; `backend/tools/migrate_sqlite_pg/mapping.py:561-581` | **Fato** | **NUL (`\x00` / `\u0000`) e violações de `CHECK`/`NOT NULL` do PG passam no `dry-run` e quebram sem diagnóstico no `--apply`:** em ensaio sintético inserindo `"bad\x00msg"` em `events.message`, `run_migration(..., apply=False)` retornou `status="dry_run_ok"`, `rejected=[]`; já `apply=True` falhou com `error_code="DataError"`, `rejected=[]` (sem indicar tabela, linha ou PK). O mesmo ocorre para `\u0000` em JSONB e strings que violam `CHECK` do PG (ex.: `detail > 120`, `evidence > 2000`, `expires_at <= approved_at`). |
| **M-04** | **Médio** | `docs/procedimento-migracao-sqlite-postgres.md:1-44` | **Fato** | **Procedimento incompleto para o dia do corte real (M1 §7):** faltam passos obrigatórios: (a) congelar gravação / encerrar app e backend antes da cópia fria; (b) inventário/preservação de mídia local separado; (c) explicitar que `horus_app` não pode rodar a migração (exige `horus_owner`/`postgres` por causa de `migration_runs` read-only e `OVERRIDING SYSTEM VALUE` em colunas `IDENTITY` em `20261117160200_horus_security.sql:52-67`); (d) explicitar que a tag `legacy/sqlite-persistence` recupera apenas o **código** e **não** desfaz dados gravados no PG após o corte; (e) explicitar que `guards.py:110-122` bloqueia qualquer destino remoto (`127.0.0.1`/`localhost` + `sslmode=disable`), exigindo habilitação controlada com TLS (`verify-full`) para Supabase no corte. |
| **M-05** | **Baixo** | `backend/tools/migrate_sqlite_pg/runner.py:131-135` | **Fato** | **Mensagem enganosa em COMMIT ambíguo:** o `except Exception` genérico sempre retorna `"Falha na migração; transação revertida."`. Se a queda de conexão ocorrer exatamente no `COMMIT` da transação (`pg_loader.py:276`), o servidor pode ter efetivado o commit, exigindo `--check-run-id` (já existente) antes de presumir rollback. |
| **M-06** | **Baixo** | `backend/tools/migrate_sqlite_pg/pg_loader.py:81-88` | **Fato** | **Checagem de destino vazio cobre só 21 tabelas de `LOAD_ORDER` + `migration_runs`:** não verifica se tabelas de `20261117160300_horus_coordination.sql` (`worker_instances`, `review_comments`, `deletion_events`, `imports`, etc.) já contêm linhas. |
| **M-07** | **Baixo** | `backend/tools/migrate_sqlite_pg/mapping.py:546`; `backend/tools/migrate_sqlite_pg/pg_loader.py:135` | **Hipótese** | **Tipos extremos:** `parse_finite_decimal()` aceita apenas `Decimal`, `int` e `str` (rejeita `float`/REAL se existir em coluna `cost_usd`/`limit_amount`); `_reseed_identity()` usa `setval(..., MAX(col), true)`, que falharia no PG se alguma linha legada tivesse `seq <= 0` (`MINVALUE = 1` da `IDENTITY`). |

---

## 2. Verificação por Critério

### (1) Guardas de origem e destino
- **Caminhos operacionais (`guards.py:16-63`):** recusa caminhos relativos, symlinks/junctions (`is_symlink() or is_junction()`), case-insensitive, `..` e subpastas de `%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus` e `Programs/Horus`. **Falha encontrada (M-01):** prefixo `\\?\` contorna `_is_inside()`. **Falha encontrada (M-02):** `--backup-dir` e `--report-out` não são validados.
- **Origem quente (`guards.py:66-75`):** recusa presença de `-wal`, `-shm` e `-journal`. Testado em `test_migration_guards_operational_hot_nonempty_and_remote`.
- **Modo somente leitura na origem (`sqlite_reader.py:58`):** abre com `mode=ro` (`uri=True`) após `validate_cold_sqlite`. Em ensaio sintético com DB em `journal_mode=WAL` (sem sidecars), o SHA-256 da origem permaneceu idêntico (`SOURCE_UNTOUCHED: True`) e nenhum `-wal`/`-shm` foi criado (`sidecars_created: False`).
- **Destino (`guards.py:78-132`, `pg_loader.py:49-89`):** recusa destino remoto, credenciais em `argv`/DSN (`password=`, `@` em URI, `passfile` fora da allowlist), `sslmode != disable` e destino não vazio (nas 21 tabelas + `migration_runs`).
- **Dry-run (`pg_loader.py:219-228`):** retorna `dry_run_ok` antes de qualquer `INSERT`/`UPDATE`, comprovado em teste (`0` linhas no PG).

### (2) Fidelidade dos dados
- **Cobertura DDL v9–v12 vs `20261117160100_horus_tables.sql`:** todas as **17 tabelas core** de `migrations.py` e as **4 tabelas Radar** (`21` no total) e todas as suas colunas estão mapeadas em `mapping.py:63-492`, sem descarte silencioso. `scripts.generator` (adicionada na v10) é preenchida com `None` quando `user_version < 10` (`sqlite_reader.py:107-108`). `radar_datasets.collected_at` usa `payload["collected_at"]` com fallback para `expires` (`sqlite_reader.py:142-147`).
- **Tipos e `*_canonical`:** `jsonb` validado (rejeita `NaN`/`Infinity` e escalares), `*_canonical` gravado como `bytea` UTF-8 exato preservando `1.0` vs `1` e ordem de chaves; `timestamptz` normalizado para UTC; `numeric` validado como `Decimal` finito `>= 0`; `seq`/`sequence` carregados com `OVERRIDING SYSTEM VALUE` + `setval(..., max, true)` (testado com `LARGE_SEQ_BASE = 5_000_000_000 > 2^31`); ordenação topológica em `jobs.depends_on` e `license_reviews.supersedes`; `workflows.current_artifact_id` carregado como `NULL` e atualizado após `artifacts`.
- **Rejeição de dados inesperados:** UTF-8 inválido, JSON inválido/escalar, timestamp inválido, `Decimal` inválido/negativo e tabelas inesperadas são rejeitados e listados em `rejected` (sem truncamento). **Ressalva (M-03):** `\x00`/`\u0000` e violações de `CHECK` do PG passam pelo `dry-run` e falham apenas no `--apply` sem popular `rejected`.

### (3) Verificação pós-carga
- `_verify_pg_against_sqlite()` (`pg_loader.py:140-183`) lê todas as linhas diretamente do PostgreSQL (`SELECT ... FROM horus.<tbl> ORDER BY <pk>`), reordena em Python pela PK e recalcula `compute_table_stats()` (`count`, `sha256` por linha e tabela com `_canonical_value`, `null_counts`, `min_id`, `max_id`, `min_seq`, `max_seq`), além de 5 queries de órfãos (`jobs_workflow`, `jobs_depends_on`, `workflows_current_artifact`, `events_job`, `license_reviews_supersedes`).
- **Prova por mutação (ensaio sintético executado nesta revisão):** ao injetar um trigger `BEFORE INSERT` em `horus.media` alterando `NEW.size := NEW.size + 1`, a migração **falhou** com `status="failed"`, `error_code="verification_failed"`, acusou divergência de `sha256` em `media` e reverteu tudo (`media_after_rb: 0`, `runs_after_rb: 0`). A verificação **não** é vacua.

### (4) Transação, falha e idempotência
- Carga, reseed, `SET CONSTRAINTS ALL IMMEDIATE`, verificação e `INSERT INTO horus.migration_runs` ocorrem em uma única transação sob `pg_advisory_xact_lock(1213157973, 164)`.
- Falha no meio da carga (`inject_failure_after="artifacts"`) e falha de verificação revertem 100% das tabelas e `migration_runs`.
- Repetição com mesmo `run_id` e mesmo `source_sha256` retorna `already_applied`; mesmo `run_id` com `source_sha256` diferente ou novo `run_id` no mesmo destino são recusados (`guard_refused`). `--check-run-id` consulta `horus.migration_runs` em modo somente leitura.

### (5) Segurança
- Zero imports de `horus_backend` em `migrate_sqlite_to_pg.py` e `migrate_sqlite_pg/*`: nenhuma autorização, produção ou inferência é executada.
- Sem segredos em `argv`, DSN, logs ou relatórios.
- Marcadores privados (`PRIVATE_MARKER_PROMPT`, `PRIVATE_MARKER_SCRIPT`) ausentes nos relatórios JSON de `dry-run`, `apply` e falha. (Em `rejected`, `pk` expõe apenas chaves primárias — em `radar_cache`, `key` pode conter termo de busca se aplicável).

### (6) Procedimento documentado (`docs/procedimento-migracao-sqlite-postgres.md`)
- Documenta dry-run, backup imutável, FKs/canonical, verificação, `--check-run-id` e restrição atual a loopback.
- **Lacunas (M-04):** não cobre congelamento de gravação/parada do app, janela de corte operacional, inventário de mídia separado, papel PG (`horus_owner`/`postgres`), retorno de código via `legacy/sqlite-persistence` sem desfazer dados PG, nem transição futura para destino remoto TLS.

### (7) Lacunas de teste (`backend/tests/pg/test_migration_sqlite_to_pg.py`)
Execução observada: **6 passed in 16.08s**. Não estão cobertos na suíte atual:
1. Teste de mutação no destino (comprovando falha de `_verify_pg_against_sqlite`, coberto apenas no ensaio desta revisão).
2. Rejeição de dados inválidos de linha: `NULL` em coluna `NOT NULL`, UTF-8 inválido, NUL (`\x00`/`\u0000`), `NaN`/`Infinity`, `Decimal` negativo, timestamp inválido, JSON escalar e ciclos em `depends_on`/`supersedes`.
3. Variações de guarda: `\\?\`, caminhos 8.3, caminhos relativos, symlinks/junctions, `--backup-dir`/`--report-out` em pasta operacional, `-shm`/`-journal`.
4. Bancos SQLite v10 e v11 e banco com tabelas vazias (0 linhas).
5. Ensaio de `pg_dump` → `pg_restore` isolado pós-migração com revalidação de contagens/hashes (M1 §7 passo 8).

---

## 3. Lista do que Corrigir Antes de Qualquer Uso com Dados Reais

1. **Corrigir `guards.py` (`M-01`, `M-02`):** normalizar/remover prefixos `\\?\` (e `\\?\UNC\`) e expandir caminhos 8.3 antes de comparar com `_operational_roots()`; aplicar validação de caminho seguro (fora de pastas operacionais e sem symlinks/junctions) também a `--backup-dir` e `--report-out`.
2. **Validar NUL e constraints de tamanho/NOT NULL no `sqlite_reader.py` (`M-03`):** rejeitar `\x00` em `str`, `\u0000` em JSON e `NULL` em colunas `NOT NULL` durante `read_and_validate_sqlite()` para que o `dry-run` detecte e popule `rejected` com tabela/linha/PK antes do `--apply`.
3. **Atualizar `docs/procedimento-migracao-sqlite-postgres.md` (`M-04`):** incluir congelamento de gravação/parada do serviço, inventário de mídia, papel PG exigido (`horus_owner`/`postgres`), política de rollback de código (`legacy/sqlite-persistence` sem desfazer dados PG) e ensaio `pg_dump`/`pg_restore`.
4. **Ajustar mensagem de exceção desconhecida no `runner.py` (`M-05`)** para orientar consulta a `--check-run-id` quando o erro ocorrer próximo ao `COMMIT`, e checar tabelas de `160300` em `_ensure_target_ready` (`M-06`).
5. **Expandir `test_migration_sqlite_to_pg.py`** cobrindo as lacunas da seção 2(7) e, em etapa própria de corte, habilitar modo remoto allowlisted com `sslmode=verify-full` e credencial via cofre/ambiente (nunca `argv`).
