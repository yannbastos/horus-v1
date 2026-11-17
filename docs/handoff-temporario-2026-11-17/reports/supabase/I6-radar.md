# HOR-164 / I6 — Radar (cache, cota, trava, CLI) e retenção em PostgreSQL

Data: 17/11/2026 · Branch: `yannbastos/sb-i6`

## 1. O que foi migrado

1. **`backend/src/horus_backend/radar_youtube/state.py`**
   - Removidos `sqlite3`, `radar-youtube.sqlite3`, `.refresh-lock.sqlite3`, DDL no construtor, `PRAGMA secure_delete=ON`, `BEGIN IMMEDIATE`/`BEGIN EXCLUSIVE`, `INSERT OR REPLACE`, `julianday` e `json_extract`.
   - `RadarState(db, *, clock=utcnow, lock_timeout=5)` exige um provedor UoW PostgreSQL (`DatabaseManager` de I1 ou pool compatível com `.connection()`), recusando caminhos/SQLite com `ValueError`.
   - `connect()` abre uma conexão via `self.db.connection()` com transação explícita (`conn.transaction()`), propagando `DatabaseRefused` (HTTP 503) quando indisponível.
   - `collection_lock(cancel=None)` usa conexão dedicada em `autocommit=True` e advisory lock de sessão (`pg_try_advisory_lock(1213157973, 16406)` / `pg_advisory_unlock`), com polling cooperativo, timeout finito e liberação garantida em `finally` ou queda de sessão/processo.
   - `reserve(bucket, limit)` serializa por bucket via `pg_advisory_xact_lock(16407, hashtext(bucket))` dentro da UoW, usando `timestamptz` em UTC para a janela móvel de 26h sem fatiamento de string.
   - `cache`/`cached`/`block`/`purge`/`save`/`load`/`list_datasets` usam as 4 tabelas `horus.radar_cache`, `horus.radar_usage`, `horus.radar_blocks` e `horus.radar_datasets` (ordenando `list_datasets` por `d.collected_at DESC, d.id DESC`).
   - `invalidate(kind, identifiers)` usa `jsonb_array_elements` + `->>` em SQL parametrizado.

2. **`backend/src/horus_backend/radar_youtube/__main__.py`**
   - CLI local conectado via `DatabaseManager(ConnectionVault(args.state_dir))` (ou `db` injetado em testes).
   - Sem conexão (`status.state != 'connected'` ou `DatabaseRefused`), emite código estável (`database_unavailable` ou `last_error_code`) em `stderr` e encerra com código `3`, sem criar `.sqlite3` ou diretório local.

3. **`backend/src/horus_backend/retention.py`**
   - Removidas todas as dependências transitivas de `sqlite3` (`Store`, `workflows.py`) e toda promessa/execução de `secure_delete`, `wal_checkpoint(TRUNCATE)` ou zeragem física imediata (documentando o modelo MVCC/WAL/backups do PostgreSQL).
   - `run_retention(db, ...)` opera via UoW de I1 (`db.connection()` + `conn.transaction()`), propagando `DatabaseRefused` (503) quando o banco está indisponível.
   - Redação lógica atualiza `horus.workflows` (`payload` + `payload_canonical`), `horus.artifacts` (`content` + `content_canonical`, preservando `content_hash`), `horus.cognitive_runs` (`request` + `request_canonical`, preservando `request_hash`), limpa erros literais em `horus.jobs`, `horus.job_attempts`, `horus.cognitive_runs` e `horus.workflow_events`, e registra `workflow.radar_redacted` em `horus.workflow_events`.

## 2. Contrato/stub pendente de I4/I5 (`scripts` / `script_receipts`)

- **`_script_literal_matches(db, workflow_id, literals, *, script_matcher=None)`** em `backend/src/horus_backend/retention.py`:
  - Mantido como stub/contrato tipado (`ScriptLiteralMatcher = Callable[[Any, str, list[str]], tuple[int, int]]`) retornando `(0, 0)` por padrão quando `script_matcher` é `None`.
  - Motivo: as tabelas/fluxos de escrita e leitura de roteiros e recibos dependem da migração I4/I5 (`scripts.py` / `production.py`).
  - Teste documentado com `@pytest.mark.skip`: `test_script_retention_contract_pending_i4_i5` em `backend/tests/pg/test_retention_pg.py`, além do teste ativo `test_script_matcher_contract_hook` validando a assinatura do gancho.

## 3. Textos de usuário / UI sobre retenção

- Busca em `frontend/src/` e `backend/src/` por `secure_delete`, `wal_checkpoint`, `zeragem`, `retenção` e `28 dias`:
  - Nenhuma string de UI em `frontend/src/` prometia `secure_delete` ou zeragem física imediata.
  - Em `backend/src/horus_backend/radar_youtube/models.py:76` permanece apenas a regra de negócio `'Retenção de fonte deve ser de no máximo 28 dias.'`.
  - Nota de documentação atualizada no docstring de `backend/src/horus_backend/retention.py` explicando que em PostgreSQL (MVCC/WAL/backups) a retenção faz redação lógica sem zeragem física imediata.

## 4. Verificações executadas

- `backend/.venv/Scripts/python.exe -m pytest backend/tests/pg/test_radar_pg.py backend/tests/pg/test_retention_pg.py -q`:
  - **93 passed, 1 skipped** (em 25.23s), incluindo testes `pg_strong` com 2 pools concorrentes para cota, `collection_lock` multi-thread e multi-processo, falha 503 sem conexão e CLI sem arquivo local.
- `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_supply_chain.py backend/tests/test_network_inventory.py backend/tests/test_api_contract_snapshot.py backend/tests/test_endpoint_auth.py -q`:
  - `test_supply_chain.py` + `test_network_inventory.py`: **43 passed, 1 skipped**.
  - `test_api_contract_snapshot.py` + `test_endpoint_auth.py`: **3 failed, 56 errors** na criação do app porque `backend/src/horus_backend/api.py:124` (escopo I-final, não editado) ainda instancia `RadarState(settings.data_dir / "radar" / "radar-youtube.sqlite3")`.
