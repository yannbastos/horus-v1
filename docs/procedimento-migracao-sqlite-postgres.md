# Procedimento de Migração Verificável: SQLite (v9–v12) → PostgreSQL

Ferramenta isolada: `backend/tools/migrate_sqlite_to_pg.py` (HOR-164 / I-mig), única exceção autorizada a importar `sqlite3`.

## 1. Guardas e restrições

- **Dry-run por padrão:** só escreve no PostgreSQL com `--apply`.
- **Origem por cópia fria explícita:** `--source-dir` (cópia completa de `data_dir`) ou `--source-db` + `--radar-db`.
  - Recusa caminhos relativos, `\\.\`, UNC, symlinks/junctions e pastas operacionais (`%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus`, `Programs\Horus`), inclusive via `\\?\`, 8.3, maiúsculas ou `..`.
  - Recusa cópia quente com `-wal`, `-shm` ou `-journal` presentes.
- **Saídas protegidas:** `--backup-dir` e `--report-out` recusam pastas operacionais, links e sobrescrita; exigem subpasta nova/vazia em `TEMP` ou pasta explicitamente allowlisted (`HORUS_MIGRATION_ALLOWLIST_DIR`).
- **Destino de ensaio local:** modo padrão `loopback`, com `127.0.0.1` e `sslmode=disable`. A carga local usa conta administrativa com as permissões necessárias para validar e gravar o schema.
- **Destino Supabase:** exige `--target-mode supabase` e `--i-have-user-authorization` inclusive no dry-run e com `--check-run-id`. Informe no DSN `host=<região>.pooler.supabase.com port=5432 dbname=postgres user=horus_app.<referência-do-projeto> sslmode=verify-full`. O transporte valida todas as respostas DNS, fixa um IP público e verifica o hostname com a CA embarcada. O DSN recusa `hostaddr`, opções adicionais e senha.
- **Credencial Supabase:** forneça `HORUS_MIGRATION_SUPABASE_PASSWORD` no ambiente do processo, fora do comando e do histórico do shell. Encerre o processo que contém a variável após o uso. O transporte recusa variáveis libpq `PG*` para impedir substituições da configuração.
- **Banco vazio:** o dry-run e a carga verificam as 21 tabelas, as tabelas de coordenação e `migration_runs`, considerando um replay com o mesmo `run_id` e hash.
- **Sem efeitos operacionais nem vazamento:** nunca executa autorização, produção ou inferência; relatórios não contêm conteúdo privado.

## 2. Fluxo de corte real (somente com autorização explícita)

- **Quem autoriza e janela:** apenas o idealizador/responsável pelo produto autoriza o corte real em janela definida.
- **Limitação do schema vigente:** o destino Supabase exige sessão como `horus_app`. Esse papel só pode ler `migration_runs`, possui acesso restrito às colunas de identidade e não pode executar o reseed das sequências. A ferramenta verifica a permissão de gravação do ledger antes da carga. O corte com `--apply` depende de uma decisão autorizada sobre o contrato de carga.
- **Checklist antes do corte:**
  1. Encerrar app, backend e workers e **congelar gravação** na origem SQLite (checkpoint WAL limpo, zero sidecars).
  2. Fazer cópia fria completa de `data_dir` para pasta isolada; inventariar e preservar mídia local/arquivos fora do PG separadamente.
  3. Gerar **backup imutável** da cópia fria via SQLite Backup API e armazená-lo em pasta dedicada protegida fora das pastas operacionais.
  4. Executar dry-run (`migrate_sqlite_to_pg.py` sem `--apply`) e confirmar `dry_run_ok`, zero `rejected` e hashes esperados.
- **Carga transacional (`--apply`):** adquire `pg_advisory_xact_lock(1213157973, 164)`, usa `DEFERRABLE` + `SET CONSTRAINTS ALL IMMEDIATE`, preserva `*_canonical` e `seq`/`sequence` (`OVERRIDING SYSTEM VALUE` + `setval`), confere contagens, `NULL`s, min/max, zero órfãos e SHA-256 por linha/tabela, e grava `horus.migration_runs` na mesma transação.
- **Checklist depois do corte:**
  1. Validar `status == "succeeded"` e relatório JSON sanitizado.
  2. Executar `pg_dump` → `pg_restore` em banco isolado descartável e revalidar contagens/hashes antes de liberar o app.

## 3. Falha, COMMIT ambíguo e política de retorno

- **Falha na carga:** rollback total automático; origem e backup imutável permanecem intocados.
- **COMMIT ambíguo:** consultar `run_id` antes de qualquer repetição (`--check-run-id <UUID>`). Se não aplicado, repetir somente em destino descartável **novo**.
- **Retorno de código:** a tag `legacy/sqlite-persistence` recupera apenas o **código** legado; **não** desfaz dados gravados no PostgreSQL após o corte.

## 4. Ensaio sintético local

```powershell
$env:PYTHONPATH = (Get-Location).Path + '/backend/src'
& backend/.venv/Scripts/python.exe -m pytest backend/tests/pg/test_migration_sqlite_to_pg.py -v
```
