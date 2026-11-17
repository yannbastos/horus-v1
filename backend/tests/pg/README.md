# PostgreSQL descartável (HOR-164 / I0)

Infraestrutura sintética: sem runtime, Supabase ou DSNs de ambiente.
Somente binários PostgreSQL; sem serviço, firewall, admin ou Docker.

```powershell
uv sync --project backend --extra test --locked
backend/.venv/Scripts/python.exe scripts/test-postgres.py -q
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 1000 --strategy fast
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 100 --strategy wal_log
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 100 --strategy file_copy
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 2900 --strategy pytest --workers 2
```

`HORUS_TEST_PG_BIN` seleciona `bin` do PG17 oficial validado com checksum publicado;
sem essa variável, usa `pgserver` (extra de teste); benchmark imprime a versão real.
PG16 do pgserver desbloqueia o protótipo, mas não comprova paridade PG17/Supabase.
Nenhum download automático ocorre na suíte.

Cada processo/worker cria `%TEMP%/horus-test-pg-*`, initdb e porta efêmera.
Trust exclusivo de 127.0.0.1 sintético; usuários locais podem acessar o cluster.
Owner NOLOGIN separado; app sem DDL/superuser/CREATEDB/CREATEROLE/BYPASSRLS/TEMP.
Padrão: clone/pool por worker, TRUNCATE RESTART IDENTITY CASCADE entre testes;
admin restaura COPY binário de sementes e sequences capturados do template validado.
`@pytest.mark.pg_strong` exige clone por teste: API real, threads, subprocessos,
múltiplas conexões/transações concorrentes; lista explícita nos testes marcados.
Reset limpa sessões com DISCARD ALL; recusa empréstimos/conexões extras/transações.
Pool fecha antes de DROP; finally para servidor e remove somente o TEMP exclusivo;
kill do processo/SO pode impedir finally. Logs existem só nesse TEMP.
Fixtures: `pg_cluster` (sessão/worker), `pg_pool` (teste, autocommit).
Cluster sintético usa fsync/synchronous_commit/full_page_writes off e buffers 16MB:
NÃO valida durabilidade/crash recovery; benchmark direto `--untuned` compara defaults.

CI proposto (owner de `.github/` implementa): `windows-latest`, checkout, setup
Python 3.12/uv, restore cache de ZIP EDB PG17 com SHA256 fixo, verificar hash,
extrair e definir HORUS_TEST_PG_BIN, uv sync --locked --extra test, executar os
dois comandos acima. Não cachear pgdata. Ubuntu pode usar binários oficiais PG17
locais; executar como usuário sem root. Sem secrets, Supabase ou rede nos testes.
Benchmark direto mede setup/clone/reset/ciclo; pytest inclui fixtures e startup/stop;
projeções são sintéticas: não incluem o trabalho real dos 2900 testes do produto.
