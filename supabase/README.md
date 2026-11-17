# Schema operacional PostgreSQL — HOR-164 / I2

As quatro migrations `*_horus_*.sql` criam 33 tabelas no schema privado `horus`.
A migration histórica HOR-139 é imutável e continua separada em `horus_sync`.
A versão esperada pelo backend é `20261117160300` (última migration operacional).
Este incremento não troca a persistência dos módulos de produto.

Aplicação: `horus_app`, sem superuser/DDL/TEMP/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS.
Dono: `horus_owner NOLOGIN`; nunca conceder esse papel ao aplicativo.
LOGIN/credencial de `horus_app` são provisionados externamente, sem senha no SQL.
O migrador autorizado precisa criar papéis, executar SET ROLE e carregar metadados;
FORCE RLS também restringe o owner: manutenção requer sessão administrativa separada.
As políticas permitem a operação da estação confiável por `horus_app`; não são
isolamento entre usuários/tenants. Data API não é habilitada por estas migrations.

O runner de manutenção aplica cada arquivo uma única vez, sob lock e transação,
e registra `schema_migrations(version, sha256(SQL_UTF8_LF_sem_BOM), applied_at)`
na mesma transação; em reaplicação confere o checksum e pula o DDL já registrado.
`app_min/app_max` ficam NULL até definir a compatibilidade do produto.
O SQL não tenta calcular o checksum de si mesmo. O app apenas lê o ledger.
O harness implementa esse contrato; o runner operacional pertence a outro incremento.

JSONB e `*_canonical bytea` são conferidos por CHECK, sem recalcular hash legado
sobre `jsonb::text`. Retenção deve redigir ambos na mesma transação.
Identity aceita carga apenas com `OVERRIDING SYSTEM VALUE`, seguida de `setval`.
FKs são DEFERRABLE INITIALLY DEFERRED; conferir SET CONSTRAINTS ALL IMMEDIATE.
Feeds globais transmissions/checkpoints/deletion usam locks antes da identity;
namespace advisory 164, chaves 1/2/3. Cadeia de exclusão usa SHA256 dos bytes
canônicos do evento sem `hash`, preservando o formato legado e seu `prev_hash`.

Validação local (extra test já instalado; nenhum login/conta Supabase):
`backend/.venv/Scripts/python.exe -m pytest backend/tests/pg -q`
O harness fornece papéis anon/authenticated e auth.users sintéticos somente no teste.
PG16.2 disponível valida o protótipo; PG15/17 e Supavisor exigem ensaio separado.
Sem Docker, `supabase db lint` local não foi executado; nenhum serviço remoto usado.
