# PostgreSQL descartável (HOR-164 / I0)

Infraestrutura sintética, sem importar persistência/configuração do produto.
Não conecta ao Supabase nem lê DSNs de ambiente. PostgreSQL precisa estar instalado
somente como binários; nenhum serviço, firewall, admin ou Docker é necessário.

```powershell
uv sync --project backend --extra test --locked
backend/.venv/Scripts/python.exe scripts/test-postgres.py -q
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 100
```

`HORUS_TEST_PG_BIN` seleciona a pasta `bin` do PostgreSQL 17 oficial já extraído
e validado contra checksum publicado pelo fornecedor. Sem essa variável, usa os
binários do `pgserver` (somente extra de teste): o benchmark imprime a versão real.
PG16 do pgserver desbloqueia o protótipo, mas não comprova paridade PG17/Supabase.
Nenhum download automático ocorre na suíte.

Cada processo/worker cria `%TEMP%/horus-test-pg-*`, initdb e porta efêmera.
Trust é exclusivo de 127.0.0.1 com dados sintéticos; não é modelo de autenticação
para produção. Usuários locais da máquina podem acessar este cluster de teste.
Owner NOLOGIN separado; app sem superuser/DDL/CREATEDB/CREATEROLE/BYPASSRLS.
DDL sintético aplicado uma vez em template fechado; clone e pool por teste.
Pool fecha antes de DROP; finally para servidor e remove somente o TEMP exclusivo.
Interrupção forçada do processo/SO pode impedir finally; não há garantia contra kill.
Logs de comandos/servidor existem só no TEMP até a limpeza.
Fixtures: `pg_cluster` (sessão), `pg_pool` (teste, autocommit).
Use `pool.connection()` e `connection.transaction()` para transações explícitas.
Não adicionar migrações do produto neste DDL de prova.

CI proposto (owner de `.github/` implementa): `windows-latest`, checkout, setup
Python 3.12/uv, restore cache de ZIP EDB PG17 com SHA256 fixo, verificar hash,
extrair e definir HORUS_TEST_PG_BIN, uv sync --locked --extra test, executar os
dois comandos acima. Não cachear pgdata. Ubuntu pode usar binários oficiais PG17
locais; executar como usuário sem root. Sem secrets, Supabase ou rede nos testes.
O benchmark mede initdb/start/DDL, média/máximo de clone e N ciclos completos;
projeção 2900 inclui pool/INSERT/drop mas exclui initdb/start/DDL únicos.
