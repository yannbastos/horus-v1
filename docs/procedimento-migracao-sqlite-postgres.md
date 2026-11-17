# Procedimento de Migração Verificável: SQLite (v9–v12) → PostgreSQL

Este documento descreve o uso da ferramenta isolada `backend/tools/migrate_sqlite_to_pg.py` (HOR-164 / I-mig), única autorizada a importar `sqlite3` no repositório.

## 1. Guardas e restrições obrigatórias

- **Dry-run por padrão:** a ferramenta só escreve no PostgreSQL quando `--apply` é informado explicitamente.
- **Origem exclusivamente por cópia fria explícita:** informe `--source-dir` (cópia completa de `data_dir`) ou `--source-db` + `--radar-db`.
  - Caminhos operacionais (`%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus`, `Programs\Horus`), symlinks e junctions são recusados.
  - Cópias quentes com sidecars `-wal`, `-shm` ou `-journal` presentes são recusadas.
- **Destino controlado:** nesta etapa aceita apenas PostgreSQL descartável em loopback (`127.0.0.1`), vazio (nenhuma das 21 tabelas populada e `horus.migration_runs` vazio) e com schema `20261117160300` aplicado. Destinos remotos, não vazios ou com credenciais em `argv`/DSN são recusados.
- **Sem efeitos operacionais:** a carga nunca executa autorização, produção, renderização ou inferência.
- **Sem conteúdo privado em logs/relatórios:** o relatório JSON traz apenas contagens, hashes, faixas de IDs/sequências e códigos de rejeição.

## 2. Fluxo de execução (passos 0–8)

1. **Prévia (dry-run):** valida origem, cria backup imutável via SQLite Backup API, executa `PRAGMA integrity_check`, `PRAGMA foreign_key_check` e valida `PRAGMA user_version` (`9..12`).
2. **Mapeamento explícito (v9–v12):** rejeita tabelas inesperadas e valida JSON, datas UTC, `Decimal` finito e dependências topológicas (`jobs.depends_on`, `license_reviews.supersedes`).
3. **Carga em transação única (`--apply`):**
   - Adquire `pg_advisory_xact_lock(1213157973, 164)`.
   - Usa `SET CONSTRAINTS ALL DEFERRED`, carrega `workflows` com `current_artifact_id = NULL`, insere pais/filhos na ordem de FKs, atualiza `workflows.current_artifact_id` após `artifacts` e aplica `SET CONSTRAINTS ALL IMMEDIATE` antes do `COMMIT`.
   - Preserva IDs `text`, `seq`/`sequence` (`OVERRIDING SYSTEM VALUE` + `setval`) e `*_canonical` byte a byte.
4. **Conferência pós-carga (na mesma transação):** compara contagens, `NULL`s, min/max de IDs e `seq`, zero órfãos e SHA-256 linha a linha (ordem de PK) e por tabela com o mesmo mapeamento canônico.
5. **Ledger e saída:** grava `run_id`, `source_sha256`, `mapping_version`, `source_schema_version` e `table_checks` em `horus.migration_runs` na mesma transação. Qualquer rejeição ou divergência causa rollback total e `exit != 0`.

## 3. Falha, COMMIT ambíguo e repetição

- Em falha, a transação é revertida integralmente e origem/backup permanecem intocados.
- Em **COMMIT ambíguo**, consulte o `run_id` antes de qualquer nova tentativa:

```powershell
python backend/tools/migrate_sqlite_to_pg.py --target-dsn "<DSN_LOOPBACK>" --check-run-id "<RUN_ID_UUID>"
```

- Se o `run_id` já constar com o mesmo `source_sha256`, a execução já foi aplicada. Caso contrário, repita apenas em um banco descartável **novo** (repetições com novo `run_id` no mesmo destino são recusadas).

## 4. Validação sintética local

```powershell
$env:PYTHONPATH = (Get-Location).Path + '/backend/src'
& backend/.venv/Scripts/python.exe -m pytest backend/tests/pg/test_migration_sqlite_to_pg.py -v
```

> **Nota:** nunca execute contra o banco SQLite operacional do usuário ou conta Supabase real sem autorização específica e janela de corte definida.
