# HOR-164 I1 — correções da revisão de segurança

Data: 17/11/2026. Branch yannbastos/sb-i1.
Commit: fb97ef5fe07ff72f65e8b2794216115c27e900bc.
Base da revisão: aa1ecd1 e 8d4c36b. Worktree limpa após commit, sem push/merge/PR.
Relatório confrontado com o código: I1-revisao-seguranca.md nesta pasta.

Todos os achados A1–A5 foram confirmados e corrigidos:

- A1: POST /v1/database/test passou a ser diagnóstico, fora da contagem/bloqueio de mutações, inclusive sob reserva de atualização; chamadas concorrentes alcançam a deduplicação do manager e retornam 200/connecting. database.changing bloqueia somente POST/DELETE connection; outras rotas não recebem mensagem incorreta de atualização. Reserva de atualização mantém o bloqueio das demais mutações.
- A2: todas as transições vault_unavailable (write, delete, read e inicialização) omitem host_label; verificado com cofre ausente/falho e com pool previamente conectado.
- A3: OperationalError na UoW adquire _operation e só fecha/transiciona se o pool ainda é o da UoW falha; duas falhas simultâneas resultam em um único teardown, sem transições concorrentes da geração.
- A4: recusa de variáveis PG* usa name.upper().startswith('PG'); testes usam mapping preservando caixa para PGSERVICE, pgservice e PgService, mesmo no Windows.
- A5: cobertura no create_app para POST test HTTP concorrente real (duas chamadas, uma tentativa), 409 somente para troca/DELETE durante changing, outra mutação local aceita; ampliados hosts negativos com maiúsculas, unicode/IDN, @, #, /, ponto final, IPv6 e decimal; transporte ASGI chunked sem Content-Length na fronteira 16 KiB e um byte acima.

O teste chunked também expôs uma falha associada: o limitador ASGI sinaliza
ClientDisconnect ao exceder o teto, e o router antes tratava isso como falha do
banco, fechando o pool e mudando o snapshot para internal_error. O payload agora
converte corpo interrompido em database_invalid_connection/422 sem alterar o
estado do banco; teste exige preservação do snapshot anterior.

Uma execução intermediária teve 77 passed, 1 skipped e falha no servidor TLS
negativo: libpq/Windows encerrou TCP com ConnectionResetError após rejeitar o
certificado. O teste agora admite alerta TLS ou reset do lado servidor e exige
explicitamente que o erro real do cliente contenha certificate verify failed,
além do código database_tls_invalid e da finalização do servidor; o critério
de rejeição do certificado foi reforçado, sem ignorar falha de verificação.

Verificações finais, cada suíte em processo separado, prefixo
backend/.venv/Scripts/python.exe:

| Comando | Resultado |
|---|---|
| -m pytest backend/tests/pg/test_database_contract.py backend/tests/pg/test_database_postgres.py -q | 78 passed, 1 skipped, 17,25 s |
| -m pytest backend/tests/test_api_contract_snapshot.py -q | 3 passed, 1,00 s |
| -m pytest backend/tests/test_network_inventory.py -q | 7 passed, 0,86 s |
| -m pytest backend/tests/test_supply_chain.py -q | 24 passed, 1,79 s |
| -m pytest backend/tests/test_endpoint_auth.py -q | 65 passed, 4 skipped, 9,82 s |

Total: 177 aprovados, 5 skips, zero falhas. Um skip aguarda integração de I2
neste worktree; quatro são os skips anteriores de symlink indisponível no
Windows. Aviso anterior de depreciação Starlette/httpx permanece documentado.
Snapshot OpenAPI inalterado. git diff --cached --check aprovado antes do commit.
Logs completos: I1-revisao-tests-database.txt, I1-revisao-tests-contract.txt,
I1-revisao-tests-network.txt, I1-revisao-tests-supply-chain.txt e
I1-revisao-tests-auth.txt, todos nesta pasta.

Somente sete arquivos autorizados alterados: api.py; database/README.md,
manager.py, router.py e transport.py; testes pg/test_database_contract.py e
pg/test_database_postgres.py. Nenhum recurso Supabase real, credencial real,
dado operacional, instalação, dependência ou outro repositório alterado.
Permanecem as pendências anteriores de integração I1+I2 e validação Supabase
sintética remota autorizada, fora deste pedido de correção.
