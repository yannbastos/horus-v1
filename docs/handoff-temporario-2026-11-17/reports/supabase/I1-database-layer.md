# HOR-164 I1 — acesso PostgreSQL, cofre e diagnóstico

Entrega em 17/11/2026, branch `yannbastos/sb-i1`, base isolada de origin/main.
Commits locais: `aa1ecd1a3fae1dc620be076e4d6f46513167829e` (implementação) e
`8d4c36ba9c7a22233f63df02f5947e7413f08220` (versão final I2). Worktree limpa.
Sem push, PR, merge, publicação, conta Supabase, dados reais ou instalação operacional alterados.

## Implementação

Pacote `backend/src/horus_backend/database/`: DTOs, configuração/cofre nativo
versionado, transporte libpq, pool síncrono, UoW, coordenação, heartbeat,
limite ASGI e roteador. A inclusão em api.py também adapta o teto do corpo e
preserva o envelope de database_busy durante reserva de atualização; demais
rotas conservam o middleware de corpo anterior e sua autenticação.

Quatro endpoints: GET status; POST/DELETE connection; POST test. Oito estados,
HTTP 200 nos diagnósticos, erros sanitizados 409/422/500/503, Cache-Control
no-store, senha somente escrita. GET retorna snapshot imutável sem rede/cofre.
POST persiste descritor+senha juntos no keyring e verifica releitura antes de
conectar; indisponibilidade de rede permite salvar, falha de cofre bloqueia.
DELETE idempotente fecha pool/coordenação e exclui a entrada, sem efeito remoto.
Backend inseguro, entrada corrompida e falhas de leitura/escrita/exclusão são
vault_unavailable, sem fallback de arquivo/env e sem credencial só em cache.

Pool 1–4 mais conexão dedicada; rotação cria classe/pool de geração nova.
Troca/exclusão fecham admissão e drenam UoWs com prazo de 3 s; recusam operação
local ativa/migração/update reservado antes da alteração. Primeiro erro de
autenticação invalida a geração e desperta fechamento fora do worker do pool.
Timeouts 5 s conexão, 3 s checkout, 15 s SQL, 2 s lock, 15 s idle transação;
fila 4, lifetime 900 s, idle excedente 60 s, check no checkout, keepalive 30/10/3.
Somente diagnóstico de leitura tem até três tentativas com backoff 1/2 s e
jitter até 250 ms; não há retry cego de transação/COMMIT/efeito externo.
Heartbeat da coordenação a cada 10 s; advisory lock compartilhado (1213157973,164)
reservado para manutenção por migrador externo com lock exclusivo correspondente.
Claim/lease/fencing de domínio continuam nos incrementos seguintes.

Papel atual deve ser horus_app, sem privilégios administrativos ou associação
privilegiada/owner/CREATE de schema ou banco. Confere grants de leitura de ledger
e identidade, ledger/version/checksum no formato SHA-256 e identidade presente.
EXPECTED_SCHEMA_VERSION final: 20261117160300, conforme nova instrução do
coordenador; nome da última migration do worktree I2 conferido somente por leitura:
20261117160300_horus_coordination.sql. Nenhum DDL de produto executado pelo app.

Allowlist autorizada pelo coordenador: um label DNS sob .pooler.supabase.com,
somente TCP 5432; hostname real do projeto pendente. Cada conexão resolve A/AAAA,
recusa qualquer IP não público e fixa hostaddr mantendo host para verify-full/SNI.
Não aceita IP literal, destino direto db.<ref>.supabase.co, lista, URI, socket,
porta alternativa ou opções arbitrárias. Configuração PG* ambiental é recusada.
Inventário F-22 e allowlist de rede atualizados por autorização explícita.

## CA pública embarcada

Supabase Root 2021 CA, validade 28/04/2021–26/04/2031, arquivo supabase-ca.crt.
Origem HTTPS verificada em 17/11/2026:
https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt
Referência oficial: https://supabase.com/docs/guides/platform/ssl-enforcement
O endereço antigo https://supabase.com/prod-ca-2021.crt retornou 404.
Fingerprint SHA-256 DER:
807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa
SHA-256 do arquivo original baixado (antes de conversões de finais de linha Git):
700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7
Certificado público sem chave privada; nenhuma CA corporativa para PostgreSQL.
Rotação exige revisão de origem/fingerprint e pacote novo, sem afrouxar TLS.

## Verificações

Ambiente: Windows, Python 3.12.14, psycopg 3.3.9, psycopg_pool 3.3.3,
PostgreSQL 16.2 descartável do harness I0/pgserver, loopback e dados sintéticos.
`uv sync --project backend --extra test --locked --system-certs` criou apenas
backend/.venv isolado; nenhum lockfile/dependência versionada alterado.

Comandos, cada suíte em processo separado (prefixo backend/.venv/Scripts/python.exe):

- `-m pytest backend/tests/pg/test_database_contract.py backend/tests/pg/test_database_postgres.py -q`: **62 passed, 1 skipped**, 18,61 s, após o ajuste final da versão I2.
- `-m pytest backend/tests/test_api_contract_snapshot.py -q`: **3 passed**, 1,16 s.
- `-m pytest backend/tests/test_network_inventory.py -q`: **7 passed**, 1,07 s.
- `-m pytest backend/tests/test_supply_chain.py -q`: **24 passed**, 2,38 s, incluindo novos arquivos já staged.
- `-m pytest backend/tests/test_endpoint_auth.py -q`: **65 passed, 4 skipped**, 12,57 s.

Total final das suítes: **161 aprovados, 5 skips, zero falhas**.
Skip I1: teste cruzado da última migration aguarda integração de I2 neste
worktree, conforme autorização do coordenador; versão final conferida também
por leitura no worktree irmão. Quatro skips auth: symlink indisponível neste
ambiente Windows. Aviso conhecido de depreciação Starlette/httpx; sem mudança
de dependências para ocultá-lo.

Cobertura: quatro endpoints/oito estados; cofre MemoryKeyring e falhas;
senha especial e validação sem reflexo; teto de corpo incluindo >2 MiB no
app autenticado; GET sem conexão ou releitura; busy/job/update; pool real e
rotação; drenagem com threads; papel/grants/schema negativos; SCRAM real com
senha sintética errada e reparo; migrating por lock real; DNS privado/misto;
resolução por conexão/hostaddr; TLS autoassinado local realmente recusado
pelo libpq com database_tls_invalid; fingerprint CA; ausência de SQLite/arquivos
no pacote. Certificado/chave do servidor negativo gerados apenas em TEMP.

Snapshot atualizado conscientemente com HORUS_UPDATE_API_SNAPSHOT=1.
Comparação estrutural confirmou todos os caminhos e modelos anteriores
idênticos; somente três novos caminhos (quatro métodos) e novos modelos.
`git diff --cached --check` aprovado, sem edição fora dos arquivos autorizados.
Logs: I1-tests-database.txt, I1-tests-contract.txt, I1-tests-network.txt,
I1-tests-supply-chain.txt, I1-tests-auth.txt nesta pasta do relatório.

Falhas intermediárias foram corrigidas e não estão ocultas: teardown do mock
keyring; tentativa de service='' do libpq rejeitada antes do TLS; glob literal
PowerShell de arquivos pytest. Um comparador auxiliar leu JSON com encoding
Windows implícito e acusou mojibake; repetido com UTF-8 explícito confirmou
snapshot somente aditivo. Os testes finais acima são os resultados válidos.

## Integração e pendências

O pacote não usa SQLite e não cria .db/.sqlite; por escopo explícito, api.py
continua instanciando os stores legados de produto até os incrementos seguintes.
Este incremento não é o corte final nem uma versão publicável com persistência
híbrida. storage.py, migrations.py e repositórios não foram alterados.

Dependências psycopg/psycopg_pool já estão no lock mas no extra test do
pyproject atual: o integrador precisa promovê-las ao runtime/empacotamento
antes de publicar o corte; esses arquivos não pertencem ao escopo de I1.

Conta Supabase real ainda necessária para provisionamento/login horus_app,
hostname exato, TLS positivo e cadeia atual do Supavisor, compatibilidade de
pool/reconexão na rede corporativa, grants/RLS reais e prova autorizada PG17.
Nenhum teste remoto, migração real, inferência, serviço pago ou upload realizado.
Migração dos domínios, schema completo I2 integrado, backup/importador e remoção
do legado pertencem aos próximos incrementos. Suíte completa/build/frontend
não executados porque não compõem a mudança de produto deste incremento.
