# HOR-164 / I2 — schema PostgreSQL versionado

17/11/2026. Branch `yannbastos/sb-i2`, base `6d511fe`.
Commit local: `bc739f26eb5d6eacd2645c6cef8b5fc4647afcae`.
Sem push, PR, merge, conta Supabase, instalação operacional ou dados reais.

## Entrega

Quatro migrations, nesta ordem:
- `20261117160000_horus_roles.sql`: papéis, schema, identidade e ledger.
- `20261117160100_horus_tables.sql`: 21 tabelas ativas, FKs, índices, tipos e bytes canônicos.
- `20261117160200_horus_security.sql`: funções, triggers, grants e RLS.
- `20261117160300_horus_coordination.sql`: JSON operacional, recibos, leases e auditoria.

Catálogo verificado por teste: **33 tabelas, 18 triggers, 110 políticas**, todas as tabelas com RLS ENABLE + FORCE.
As 33 tabelas incluem as 21 ativas e 12 adicionais do plano/emendas; a cadeia histórica HOR-139 permanece imutável em `horus_sync` e não entra nesses totais.

IDs legados text; datas timestamptz; JSONB com canonical bytea nos campos de comparação/hash; valores monetários numeric finitos/não negativos; identity ALWAYS e carga administrativa com OVERRIDING SYSTEM VALUE/reseed.
FKs diferidas permitem a carga cíclica; preservados CHECKs legados, inclusive local_pilot, origin e generator.
Bytes canônicos são comparados ao parse JSONB por CHECK e não recalculados a partir de jsonb::text.

`horus_app` não é owner, não herda outros papéis e não tem superuser, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS, DDL, TEMP ou TRUNCATE.
O owner é `horus_owner NOLOGIN`; autenticação/login de produção não são provisionados aqui.
PUBLIC/anon/authenticated sem grants no schema operacional, funções ou tabelas/sequences.
Funções SECURITY INVOKER com search_path=pg_catalog e EXECUTE público revogado.
Checkpoint concede UPDATE só em consumed_at/consumed_ref, com trigger de consumo único e validade pelo relógio PG.
Append-only tem barreiras independentes de grants e triggers; app não pode fornecer identity explícita.
Locks de statement antecedem identity dos feeds globais (namespace 164, chaves 1/2/3: transmissions/checkpoints/deletion).
Cadeia de exclusão confere prev_hash e SHA256 dos bytes canônicos originais, sem hash de JSONB.

README de 35 linhas em `supabase/README.md`; nenhuma alteração em backend/src, repositórios do produto, storage.py, migrations.py, instalador ou frontend.

## Validação executada

Usado o venv já existente da raiz principal, pois backend/.venv não existe neste worktree; nenhuma dependência instalada.

```powershell
$env:PYTHONPATH = (Get-Location).Path + '/backend/src'
& 'C:/Users/yannn/01. Projetos/HORUS/horus/backend/.venv/Scripts/python.exe' -m pytest backend/tests/pg -q -ra --tb=short
```

**108 passed, 1 skipped em 46,78 s**, incluindo os testes existentes do harness.
Log: `I2-tests.txt` nesta pasta.

Cobertura: fresh apply incluindo histórico com papéis/auth.users sintéticos apenas no harness; idempotência/checksums; rollback atômico de DDL+ledger; todas as tabelas/colunas/tipos, UQs/FKs e identity; grafo cíclico sintético; CHECKs negativos e JSON/canonical divergente; distinção canônica 1/1.0; RLS FORCE inclusive contra owner e negação a outros papéis; DDL/TEMP/TRUNCATE/schema/funções negados; append-only; checkpoint com duas sessões/um vencedor e consumo repetido/expirado recusado; ordering de identity com duas sessões e lock antes do contador.
Casos pg_strong recebem clone exclusivo; demais casos reutilizam o clone do módulo com TRUNCATE administrativo entre casos.
`git diff --check` e `git diff --cached --check` aprovados; Git limpo após commit.

## Contratos e limites para integração

- **I1 deve fixar EXPECTED_SCHEMA_VERSION = "20261117160300".** O único skip é o teste cruzado, pois backend/src/horus_backend/database/expected_schema.py ainda não está nesta branch; passa a executar automaticamente após integração de I1.
- O checksum do ledger é SHA256 do arquivo SQL inteiro normalizado UTF-8/LF sem BOM (testado contra CRLF/BOM), estável entre Windows/Linux.
- O runner de manutenção insere o ledger na mesma transação do DDL, confere checksum antes de pular arquivos aplicados e usa conta administrativa separada; o helper de teste implementa esse contrato. SQL não contém checksum autorreferente. O runner operacional pertence aos incrementos seguintes; startup não migra.
- migration_runs é gravado pelo migrador na mesma transação da carga; app só lê. Nenhuma carga real foi realizada.
- Políticas TO horus_app permitem a estação confiável; não pretendem isolamento multiusuário/tenant. Data API não foi habilitada.
- Este incremento entrega apenas schema e testes; módulos de produto continuam SQLite durante o desenvolvimento, conforme escopo aprovado.
- Harness local usa PG16.2/pgserver sintético, não valida durabilidade/crash nem paridade PG15/17. Ensaio com binários PG17 permanece necessário para a validação final.
- `supabase db lint` não executado: modo local exige Docker, ausente; não houve login ou comando remoto.
- Dependem de conta Supabase real/autorização posterior: provisioning seguro de login/roles e recursos, aplicação remota, TLS/Supavisor, grants efetivos do provedor e validação de paridade/ledger. Nenhuma senha, token ou chave foi solicitada, escrita ou registrada.
