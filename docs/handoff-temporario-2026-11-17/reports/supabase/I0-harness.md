# HOR-164 I0 — harness híbrido e benchmark (continuação)

Atualizado em 17/11/2026. Commit desta continuação: fd8c662; base do primeiro incremento: 0fd7e90, na branch yannbastos/sb-i0-harness. Nenhum push, PR ou merge; runtime e instalação real intocados.

## Resultado recomendado

Padrão rápido: um cluster e um clone/pool por sessão/worker, com reset completo entre testes; usar dois workers quando houver volume suficiente. Somente a lista explícita de testes com `@pytest.mark.pg_strong` recebe clone exclusivo por teste: API real, threads, subprocessos, múltiplas conexões/transações concorrentes. No protótipo, os testes marcados estão em backend/tests/pg/test_harness.py. A escolha final por worker evita também 104 custos de clone, um por módulo atualmente existente no backend.

Reset administrativo: verifica empréstimos/transações/conexões externas; executa DISCARD ALL em todas as conexões do pool (sem prepared statements cacheados), TRUNCATE das tabelas horus RESTART IDENTITY CASCADE, COPY binário das sementes capturadas do template validado e restauração de last_value/is_called das sequences. App permanece horus_app sem DDL/TEMP/superuser/CREATEDB/CREATEROLE/BYPASSRLS; owner NOLOGIN separado. O snapshot pertence ao DDL sintético atual (FK deferred); futuras migrations reais precisam validar sementes/ordem de FKs reais, sem presumir equivalência automática.

Mantidas allowlist de DSN com host/hostaddr loopback literais, porta/banco registrados da sessão, sem DNS/DSN do produto; TEMP exclusivo prefixado com validação antes da remoção; CREATE_NO_WINDOW, stdin nulo, stdout/stderr em arquivo, sem pipes herdados; finally fecha pool/conexões antes de DROP e encerra o servidor. Kill do processo/SO pode impedir finally. Trust local permite acesso de outros usuários da máquina; não é autenticação de produção.

Configuração rápida é gravada SOMENTE no novo pgdata sintético: fsync=off, synchronous_commit=off, full_page_writes=off, shared_buffers=16MB. Não comprova durabilidade/crash recovery. Cluster(tuned=False) / benchmark --untuned conserva os defaults. Nenhuma configuração global ou instalação real foi alterada.

## Ambiente e comandos

Windows, Python 3.12.14; pgserver 0.1.4 fornece PostgreSQL 16.2; psycopg/psycopg-binary 3.3.9, psycopg_pool 3.3.3, pytest-xdist 3.8.0 e execnet 2.1.2. Dependências somente no extra test e fixadas em backend/uv.lock.

```powershell
uv sync --project backend --extra test --locked --system-certs
backend/.venv/Scripts/python.exe scripts/test-postgres.py -q
backend/.venv/Scripts/python.exe scripts/test-postgres.py -q -n 2
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 1000 --strategy fast
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 1000 --strategy fast --untuned
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 100 --strategy wal_log
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 100 --strategy file_copy
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 30 --strategy wal_log --untuned
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 2900 --strategy pytest --workers 0
backend/.venv/Scripts/python.exe scripts/test-postgres.py --benchmark 2900 --strategy pytest --workers 2
```

## Medições

| Estratégia | N | Por caso | 2900 casos | Observação |
|---|---:|---:|---:|---|
| Primeiro I0, clone por teste | 30 | ~802 ms | 2326,3 s / 38,77 min | Antes desta continuação |
| Clone wal_log, tuned | 100 | 723,9 ms | 2099,4 s / 34,99 min | Clone médio 471,1 ms |
| Clone file_copy, tuned | 100 | 669,1 ms | 1940,5 s / 32,34 min | Clone médio 441,7 ms |
| Clone wal_log, defaults | 30 | 671,9 ms | 1948,5 s / 32,48 min | Clone médio 436,7 ms |
| Reset rápido final, tuned | 1000 | 21,13 ms | 61,28 s / 1,02 min | Reset médio 15,16 ms |
| Reset rápido final, defaults | 1000 | 23,66 ms | 68,62 s / 1,14 min | Reset médio 17,38 ms |
| Pytest rápido, sequencial | 2900 | 24,65 ms | **71,50 s / 1,19 min** | Medido, não extrapolado; inclui processo/setup/stop |
| Pytest rápido, xdist 2 workers | 2900 | 14,62 ms agregados | **42,40 s / 0,71 min** | Medido, não extrapolado; ~41% menos tempo |

Benchmark direto final tuned: initdb 3,077 s, start 0,332 s, DDL+snapshot 0,073 s, clone único 0,419 s, 1000 ciclos 21,131 s e total com limpeza 25,710 s. Defaults: initdb 3,038 s, start 0,464 s, DDL 0,076 s, total com limpeza 28,583 s. Todos os ensaios diretos confirmaram removed=true.

O ganho principal vem do reset/reuso, não de desabilitar durabilidade nem de trocar a estratégia de clone. File_copy foi um pouco mais rápido que wal_log no ensaio, mas variação entre rodadas/defaults é da mesma ordem; mantive wal_log como padrão explícito e file_copy disponível no benchmark. As primeiras medições de clones concorreram parcialmente com outros ensaios; as medições finais fast/defaults, pytest seq e x2 ocorreram sem outro benchmark simultâneo. Não interpretar diferenças pequenas como causalidade garantida.

Os 2900 casos gerados executam INSERT/RETURNING + reset e as fixtures globais reais, com dados mínimos, em um módulo. Não são os 2900 testes funcionais do produto migrados; o trabalho real das funções não está incluído. A mudança final de escopo módulo para sessão/worker não altera o número de clones desses ensaios de módulo único; foi seguida pela suíte focada x2 aprovada. Para vários módulos, sessão/worker elimina clones adicionais.

Metas do custo do harness sintético atendidas com ampla margem (<15 min seq e <6 min x2). Somar aproximadamente 71,5 s à referência informada de 10,5 min sugere ~11,7 min sequencial se todo o restante permanecesse igual, mas isso é apenas modelo, não validação do produto migrado. Para 5% de casos fortes, modelo sequencial aproximado: 2755×0,02465 + 145×0,7239 ≈173 s de infraestrutura; somado a 630 s ≈13,4 min, sem garantir custo de domínios reais. A proporção forte deverá refletir requisitos de isolamento, não ser reduzida para cumprir meta. O paralelismo do produto completo ainda precisa ser medido na migração; o xdist daqui demonstra isolamento/ganho de throughput do harness.

## Verificação

- Suíte focada: 19 passed em 15,35 s (seq); após a escolha final por worker, 19 passed em 11,90 s com -n 2.
- Benchmarks pytest finais: 2900 passed sequencial e 2900 passed com -n 2, returncode=0.
- Coleta conjunta final: 2889 tests collected em 0,86 s, sem erro; suíte legada não foi executada.
- Casos novos provam sementes/identity repetíveis entre quatro testes, limpeza de GUC/advisory lock, clone exclusivo por marcador, recusa de conexão emprestada e recusa de conexão externa idle.
- Mantidos negativos de DSN remoto, privilégios mínimos, isolamento/drop/finally e smoke identity/OVERRIDING SYSTEM VALUE/jsonb/append-only/FK deferred/ON CONFLICT RETURNING.
- uv lock --check --offline e git diff --check aprovados; README 40 linhas.

Artefatos completos neste diretório: horus-i0-hybrid-final-{seq,x2}.json, horus-i0-hybrid-fast1000{,-untuned}.json, horus-i0-wallog100.json, horus-i0-filecopy100.json, horus-i0-hybrid-wallog30-untuned.json, horus-i0-hybrid-tests*.txt e horus-i0-hybrid-collection.txt. Execução normal não gera os 2900 casos de benchmark; eles são opt-in via --benchmark/--pg-benchmark-cases.

## PG17 oficial e paridade

Adicionado scripts/test-postgres-download.ps1 (PowerShell 7), executado em segundo plano oculto, com Invoke-WebRequest -OutFile -Resume -PassThru, duas tentativas e ConnectionTimeoutSeconds=600 / OperationTimeoutSeconds=600 por tentativa. Resume usa Range quando existem bytes parciais; não houve bytes para retomar nesta máquina. O script não extrai nem executa artefatos e lembra a comparação com checksum publicado antes de uso. TLS/certificados do Windows foram mantidos, sem bypass.

Fonte oficial já conferida na primeira entrega: https://www.enterprisedb.com/download-postgresql-binaries anuncia 17.12 e aponta o Windows x64 para https://sbp.enterprisedb.com/getfile.jsp?fileid=1260667. Tentativa 1 começou 14:17:54 e retornou HTTP 500 (handshakefailed) às 14:19:57; tentativa 2 retornou o mesmo HTTP 500 às 14:22:09. O servidor/proxy falhou antes do timeout de 10 min; não foi timeout curto do cliente. Logs: horus-i0-pg17-download{,-error}.log; processo encerrado, sem download pendente. Uma tentativa alternativa de lançamento para o domínio direto foi rejeitada pela revisão automática, que informou somente blocked by policy; não foi contornada.

Nenhum ZIP PG17 foi obtido, nenhum checksum publicado foi confirmado, nenhum binário PG17 foi extraído/executado e não há resultado de paridade PG17 versus 16.2. HORUS_TEST_PG_BIN continua sendo o ponto de seleção depois da obtenção e validação externa do ZIP. PG16.2 funciona como protótipo autorizado; PG17 ainda bloqueia o aceite definitivo de paridade. Nada foi conectado/criado/alterado no Supabase, nem dados reais transmitidos.

## CI proposto / próximos passos

Owner de .github implementa depois: windows-latest, Python 3.12/uv, ZIP PG17 oficial fixado com checksum publicado validado, cache por SHA256 só de binários, HORUS_TEST_PG_BIN, uv sync --locked --extra test, suíte -q -n 2 e benchmark pytest sintético opt-in. Nunca cachear pgdata nem usar secrets/Supabase. Nenhum workflow criado neste incremento.

Revisar o commit; obter PG17 verificável e repetir smoke/benchmarks; validar duração e proporção de pg_strong com os domínios realmente migrados. Para durabilidade/crash recovery, usar cluster untuned e casos fortes específicos. Não migramos módulos/dados nem alteramos produção.
