# HOR-164 — Revisão Independente de Segurança do I4 (PostgreSQL)

- **Data:** 17/11/2026
- **Escopo revisado:** `yannbastos/sb-i4` (commit `e042d15`) vs `origin/main` / `yannbastos/pg-integration` (`backend/src/horus_backend/{human_checkpoints,transmissions,security,workflow_hermes,workflows}.py`, `supabase/migrations/20261117160100_horus_tables.sql`, `20261117160200_horus_security.sql` e `backend/tests/pg/test_{checkpoints,transmissions,budget,workflows}_pg.py`).
- **Base normativa:** `AGENTS.md`, `docs/AGENT-GOVERNANCE.md`, `M1-plano-v2-emendas.md` (emendas A1, A3, A4) e comportamento SQLite anterior em `origin/main`.
- **Modo:** somente leitura no repositório (nenhum arquivo do repositório alterado; sem rede/Supabase real/Hermes real).

## Veredito

**APROVADO**

Nenhuma regra de segurança, isolamento, imutabilidade, auditoria pré-envio ou teto financeiro foi enfraquecida na migração de SQLite para PostgreSQL. As barreiras críticas (consumo único de checkpoint, orçamento global sob advisory lock, auditoria estrita pré-envio, append-only de `transmissions` e equivalência dos 10 `BEGIN IMMEDIATE` de `workflows.py`) foram preservadas ou endurecidas com triggers, `GRANT`s de coluna e locks explícitos.

---

## Tabela de Achados

| ID | Severidade | Tipo | Arquivo:Linha | Descrição e impacto |
|---|---|---|---|---|
| F1 | Baixo | Fato | `backend/src/horus_backend/workflows.py:557, 939, 1039` (e `:544`) | Verificação de integridade de `artifacts` usa `digest(_json_val(source["content"])) != source["content_hash"]` sobre `jsonb` em vez de `sha256(bytes(source["content_canonical"]))`. Mitigado pelo `CHECK (content IS NOT DISTINCT FROM convert_from(content_canonical,'UTF8')::jsonb)` em `horus_tables.sql:57`, mas menos estrito que a emenda A4 para distinções como `1` vs `1.0`. |
| F2 | Baixo | Fato | `backend/src/horus_backend/workflows.py:919` | `authorize()` compara `radar["expires_at"]` com `datetime.now(timezone.utc)` (relógio do SO) em vez de `pg_clock(conn)`. Não afeta TTL de checkpoints nem orçamento (que usam `pg_clock`), mas fica sujeito a drift de relógio local na expiração de datasets Radar. |
| F3 | Baixo | Fato | `backend/src/horus_backend/human_checkpoints.py:6` | Docstring ainda menciona "numa transação do SQLite" apesar de a implementação já ser 100% PostgreSQL. Sem impacto funcional/segurança. |

---

## Verificação dos 7 Pontos de Segurança

### 1. Consumo único de checkpoint (`human_checkpoints.py`, emenda A1)
- **Duas sessões, um vencedor:** `_acquire_checkpoint_lock(conn, action, subject)` (`human_checkpoints.py:86-87`) adquire `pg_advisory_xact_lock(16411, hashtext(f"{action}:{subject}"))` em `grant` (`:136`) e `consume` (`:207`), combinado com `SELECT ... FOR UPDATE` (`:210-216`) e `UPDATE ... WHERE id = %s AND consumed_at IS NULL AND expires_at > %s ... RETURNING *` (`:240-249`), além do trigger `horus.consume_once()` (`20261117160200_horus_security.sql:23-37`).
- **Mais recente sem fallback permissivo:** A query usa `ORDER BY seq DESC LIMIT 1 FOR UPDATE` filtrando apenas `action`, `subject`, `consumed_at IS NULL` e `expires_at > moment` — não filtra por `limit_amount`/`currency`. Se o último válido tiver limite insuficiente ou moeda divergente, recusa com `checkpoint_limit_exceeded` (`:233-237`) sem cair para aprovação anterior mais permissiva.
- **Relógio do servidor e `ClockProvider`:** `pg_clock(conn, clock=self.clock)` (`security.py:367-376`) executa `SELECT clock_timestamp()` em produção; `clock` é injetável apenas no construtor para testes. `moment` é lido após o lock (`human_checkpoints.py:208`) e relido em `t_final` (`:238`) antes do `UPDATE`, além de o trigger `consume_once()` validar `clock_timestamp() >= OLD.expires_at`.
- **Teto do backend no `grant`:** `grant()` valida `cost_reservation(self.settings)` para `hermes_run` e `hermes_script` e recusa moeda != `USD` ou `amount > ceiling` com `checkpoint_limit_exceeds_backend_ceiling` (`:117-123`).

### 2. Orçamento global (`security.py`, `workflow_hermes.py`, `workflows.py`, emenda A3)
- **Lock global em todas as escritas:** `acquire_budget_lock(conn)` (`pg_advisory_xact_lock(16412, 1)`, `security.py:358-364`) é adquirido em:
  - `HermesStages.analysis` (`workflow_hermes.py:260`) antes de `daily_runs`, `daily_tokens`, `require_budget` e `INSERT INTO horus.cognitive_runs`;
  - `HermesStages._record` (`workflow_hermes.py:190`) antes de atualizar status/usage de `cognitive_runs`;
  - `require_budget` (`security.py:469`);
  - `WorkflowService.recover` (`workflows.py:820`) antes de marcar `submitting`/`admitted` como `unknown`.
- **Ordem global de locks (sem deadlock):** orçamento (`16412, 1`) → raiz `horus.workflows FOR UPDATE` → `jobs` / `job_attempts` → `checkpoint` (`16411, hashtext`). Em `recover()`, a UoW do lock de orçamento fecha antes da chamada a `_finish()`.
- **Dia em UTC sem `[:10]`:** `_utc_day_bounds()` (`security.py:379-395`) calcula `[day_start, day_start + 1 day)` em UTC; `budget_usage`, `daily_tokens` e `daily_runs` (`:419-525`) usam intervalos tipados e mantêm reservas abertas/`unknown` atravessando a meia-noite UTC.

### 3. Auditoria pré-envio e prevenção de replay (`workflow_hermes.py`, `workflows.py`)
- **Bloqueio por falha de auditoria:** `HermesStages._admit` (`workflow_hermes.py:322-325`) chama `self._audit_submit(run_id, "attempted", body, strict=True)` (`TransmissionLog.record`). Qualquer exceção grava `status='failed'` e levanta `CognitiveFailure` sem chamar `client.submit`.
- **Bloqueio por falha de checkpoint:** `_claim_tx` (`workflows.py:606-613`) consome o checkpoint dentro da mesma UoW que marca o job `running`; qualquer recusa reverte a transação.
- **`unknown` / replay:** `retry()` (`workflows.py:885-889`) bloqueia enquanto houver `cognitive_runs.status = 'unknown'`. `reconcile()` (`:986-1013`) e `_claim_tx` (`:575-587`) retomam a execução via `resume_run` sem nova autorização, sem novo checkpoint e sem nova reserva de orçamento; `analysis()` (`:300`) só chama `_admit` se `external_ref` estiver ausente, mantendo a mesma `idempotency_key`.

### 4. Imutabilidade (append-only) de `transmissions`
- `20261117160200_horus_security.sql:13-14, 40-44` instala `transmissions_append_lock`, `transmissions_no_update` e `transmissions_no_delete` (`ERRCODE='23514'`).
- `protect_tables()` (`:53-85`) força RLS e concede apenas `SELECT` e `INSERT` (sem `UPDATE` nem `DELETE`) ao papel `horus_app`.

### 5. Migração dos 10 `BEGIN IMMEDIATE` de `workflows.py`
Todos os 10 blocos `BEGIN IMMEDIATE` do legado em `origin/main` foram migrados para locks equivalentes em `store.uow()` sem introduzir TOCTOU entre leitura de estado e escrita:
1. `create_manual` (`:362-363`): `pg_advisory_xact_lock(16413, hashtext(key))`
2. `create` (`:484-485`): `pg_advisory_xact_lock(16413, hashtext(key))`
3. `_claim_tx` (`:526-532`): `workflows FOR UPDATE` → `jobs FOR UPDATE` (+ `cognitive_runs`/`authorizations FOR UPDATE` + checkpoint lock)
4. `_finish` (`:658-670`): `workflows FOR UPDATE` → `job_attempts FOR UPDATE` → `jobs FOR UPDATE`
5. `run_step` (`:755-757`, tratamento de erro): `workflows FOR UPDATE`
6. `recover` (`:819-820`): `acquire_budget_lock(conn)`
7. `cancel` (`:842-844`): `workflows FOR UPDATE`
8. `retry` (`:868-870`): `workflows FOR UPDATE`
9. `authorize` (`:912-932`): `workflows FOR UPDATE` → `jobs FOR UPDATE` (+ checkpoint lock via `grant(..., db=conn)`)
10. `reconcile` (`:988-990`): `workflows FOR UPDATE`
11. `review` (`:1019-1021`): `workflows FOR UPDATE` (11º bloco em `sb-i4` cobrindo o 10º `BEGIN IMMEDIATE` original).

### 6. 503 estável, sem fallback silencioso e sem `sqlite3`
- Nenhuma referência a `sqlite3` ou fallback local nos 5 arquivos migrados.
- `Store.uow()` propaga `DatabaseUnavailable` (`http_status=503`, `code="database_unavailable"`), validado nos 4 arquivos de teste PG. `TransmissionLog.best_effort()` mantém tolerância a falhas apenas para auditoria não bloqueante de recusas/desfechos.

### 7. Cobertura e força dos testes PG
- **Comando executado no worktree `sb-i4`:**
  `backend/.venv/Scripts/python.exe -m pytest backend/tests/pg/test_checkpoints_pg.py backend/tests/pg/test_transmissions_pg.py backend/tests/pg/test_budget_pg.py backend/tests/pg/test_workflows_pg.py -v --tb=short`
- **Resultado observado:** **77 passed in 24.17s** (0 falhas, 0 skips).
- Os testes exercitam recusas reais (não só caminho feliz), concorrência real com `ThreadPoolExecutor` + `threading.Barrier` (`@pytest.mark.pg_strong`), triggers/`GRANT`s de append-only e propagação de `DatabaseUnavailable` (503).
