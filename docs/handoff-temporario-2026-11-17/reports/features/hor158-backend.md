# HOR-158 — limites de checkpoint no backend

Data: 17/11/2026. Branch: yannbastos/hor158-backend. Base: 60e14265cbfee77463175b993dac4cc92922bdfb.

## Contrato e comportamento

O contrato aditivo solicitado foi preservado. Exemplo parcial de GET /v1/security/status com preços, orçamento e tetos válidos:

```json
{"hermes":{"cost_limits":{"reservation_per_run_usd":"0.32768"},"checkpoint_limits":{"default_ttl_seconds":900,"max_ttl_seconds":3600}}}
```

A reserva é calculada por cost_reservation(settings) e serializada com format(..., 'f'), nunca float. Sem preços, orçamento ou tetos válidos, reservation_per_run_usd é null; os gates existentes continuam recusando execução real. Os TTLs expostos e os limites de CheckpointRequest usam as constantes de human_checkpoints.py.

CheckpointService.grant (backend/src/horus_backend/human_checkpoints.py:83-90) recusa concessões monetárias de hermes_run/hermes_script acima da reserva do backend ou em moeda diferente de USD com CheckpointRefused.code = checkpoint_limit_exceeds_backend_ceiling, antes de inserir ou reaproveitar recibos. A concessão monetária também exige configuração disponível e válida. O serviço recebe settings nos caminhos de API, workflow, roteiro e narração; a verificação também vale com db= dentro de transações externas. O TTL continua restrito a inteiros em [30, 3600], no serviço e na validação HTTP.

POST /v1/checkpoints já aceita somente paid_narration/publish: hermes_run/hermes_script continuam recusados com HTTP 422, inclusive quando o cliente tenta enviar um valor maior. Não foi ampliado o schema para permitir concessões cognitivas diretas. A tradução de CheckpointRefused nessa rota retorna HTTP 409 com código estável; os fluxos cognitivos continuam nas autorizações existentes, que calculam o valor pelo backend.

## Lacunas preservadas

paid_narration e publish não possuem teto monetário configurável por ação. O preço por mil caracteres do ElevenLabs é preço, não teto; os orçamentos de tokens/execuções em security.py são do Hermes. Foi mantido o comportamento existente para essas ações, com comentário no código e testes de concessão/consumo; consume continua recusando custo acima do limite aprovado, moeda incompatível, expiração e reuso. Não foram inventados novos campos ou limites. hermes_script atualmente concede um checkpoint sem valor e consome sem amount; seu gate orçamentário cognitivo existente permanece separado, e uma eventual concessão monetária agora recebe o mesmo teto de reserva do Hermes.

## Testes e isolamento

Novos testes em test_human_checkpoints.py cobrem valor maior (com e sem transação, duas ações cognitivas), igualdade/valor menor, outra moeda, configuração ausente, ações sem teto, TTL acima do máximo e recusa de concessão cognitiva direta pela API. test_security_budget.py cobre status completo com decimal string e cada campo de configuração ausente; só foi ajustada a expectativa preexistente de chaves exatas.

Comando: python -m pytest backend/tests contracts/hermes/tests -q, em execução única conjunta, incluindo checkpoints, orçamento, snapshot OpenAPI, inventário de rede, supply chain, autenticação e adversariais. Interpretador reutilizado do ambiente existente da checkout principal, com PYTHONPATH apontando para backend/src desta worktree e HORUS_DATA_DIR=%TEMP%/hor158-backend-tests; fixtures usam bancos/perfis e transportes sintéticos em diretórios temporários. Não houve instalação de dependências, app instalado, dev server operacional, inferência ou serviço pago. O único arquivo solicitado fora da worktree é este relatório; nenhum perfil operacional foi acessado para execução.

Log: C:\Users\yannn\AppData\Local\Temp\1\hor158-backend-pytest.log.

Resultado da execução conjunta única: 2923 passed, 37 skipped, 1 warning, 1 error e 14 subtests passed em 750,63 s. Os grupos obrigatórios de checkpoint, orçamento, snapshot, inventário de rede, supply chain, autenticação e adversariais não falharam. A suíte completa NÃO ficou totalmente verde: test_project_import.py::test_a_junction_or_symlink_package_is_resolved_not_trusted teve erro de setup em test_exports.py:23, StopIteration ao procurar um artefato scene no fixture de produção, antes de executar a asserção do teste. Não foi alterado nenhum desses arquivos, nem mascarada a falha. A causa da ausência do artefato na execução conjunta não foi comprovada.

Foi repetido somente esse teste para investigar o erro: 1 skipped em 9,21 s; o fixture de produção desta vez passou, e o teste alcançou seu skip existente em test_project_import.py:292 (symlink indisponível sem privilégio). Essa repetição não prova a asserção que exige symlink; a limitação permanece explícita e cabe à integração considerar a evidência. Log da repetição: C:\Users\yannn\AppData\Local\Temp\1\hor158-backend-recheck.log.

Snapshot OpenAPI: +0/-0, sem alteração; /v1/security/status não possui response_model tipado. HORUS_UPDATE_API_SNAPSHOT não foi usado. git diff --cached --check passou antes do commit, e a worktree terminou limpa, sem frontend alterado.

Commit: 45c519fd87bd2726590749e33f66b95cbaf2a0a5 — fix(checkpoints): aplicar tetos do backend e expor limites de aprovação. Oito arquivos, 102 adições e 10 remoções. Sem push, PR ou merge. Revisão/integração ficam com o coordenador, considerando a falha de fixture e o skip de symlink acima.


## Continuação — comparação com main limpa (task_a59fe63ed921)

Foi criada uma worktree detached fora do repositório, em %TEMP%/hor158-clean-baseline, exatamente de 60e14265cbfee77463175b993dac4cc92922bdfb. git status --porcelain estava vazio antes e depois da execução. Ela foi removida com git worktree remove, sem force; git worktree list confirmou a remoção. A branch de implementação permaneceu em 45c519fd87bd2726590749e33f66b95cbaf2a0a5, limpa e sem mudanças nesta investigação.

Comando de ambas as rodadas: python -m pytest backend/tests contracts/hermes/tests -q --basetemp <diretório exclusivo em TEMP>. O argumento basetemp apenas preserva os dados da execução fora da limpeza rotativa padrão do pytest. Mesmo interpretador da entrega anterior, PYTHONPATH apontando para backend/src da checkout correspondente e HORUS_DATA_DIR exclusivo em TEMP; sem perfil operacional, rede externa ou inferência.

| Rodada | Aprovados | Skips | Erros | Subtestes aprovados | Tempo |
|---|---:|---:|---:|---:|---|
| Branch, entrega original | 2923 | 37 | 1 | 14 | 750,63 s |
| Main pura 60e1426 | 2904 | 38 | 0 | 14 | 678,27 s |
| Branch 45c519f, repetição conjunta | 2923 | 38 | 0 | 14 | 1104,35 s |

Todas as rodadas tiveram o mesmo warning de serialização proposital em test_production_contract. A diferença main × branch é exatamente 19 casos adicionados. O único erro original virou o skip existente, sem mudança no código. Foram feitas também 30 execuções isoladas do caso de symlink na main pura: todas terminaram em skip, sem erro de setup. Uma tentativa inicial de fornecer 30 node IDs idênticos ao pytest foi deduplicada em um único skip e não foi contada nessas 30 execuções.

Dados consultados somente para leitura nos SQLite temporários de test_a_junction_or_symlink_pac0: tanto a main quanto a branch registraram production/job com status succeeded, error null e três artefatos scene. Os result.json de ambas as rodadas também contêm cenas produzidas pelo worker daquela própria produção. Não houve evidência de artefato fornecido por outro teste nem de estado compartilhado responsável pela falha.

A causa imediata do traceback original está comprovada: backend/tests/test_exports.py:19 recebe de finished() um estado terminal; backend/tests/test_production.py:153-157 aceita também failed/blocked/interrupted; backend/tests/test_exports.py:23 usa next sem verificar sucesso ou existência de cena, gerando StopIteration. Isso é fragilidade diagnóstica preexistente do fixture, mas NÃO prova por que a produção original ficou sem cena. backend/src/horus_backend/production_worker.py:449-455 cria e registra as cenas da própria produção. O privilégio de symlink é verificado apenas em backend/tests/test_project_import.py:289-292, DEPOIS do fixture: explica os skips observados, não explica o erro de setup anterior.

Proposta mínima, não aplicada: acrescentar assert record['status'] == 'succeeded', record logo após finished() em test_exports.py:19, antes de procurar scene; assim uma eventual falha de produção exibirá seu diagnóstico original em vez de StopIteration. Isso melhora a evidência, não é apresentado como correção da causa desconhecida. Os módulos de produção e esses testes são idênticos entre a base e a branch; não foi encontrada ligação causal com as mudanças do HOR-158.

Veredito: INCONCLUSIVO quanto à origem da falha original. Ela não ocorreu na main pura nem voltou a ocorrer na branch, portanto não há evidência suficiente para classificá-la como preexistente ou como regressão da implementação. Não foi aplicada uma alteração especulativa para atribuir causalidade a uma coincidência entre execuções. A comparação pedida foi executada; a prova da causa e o veredito binário solicitado permanecem não alcançados.

Logs e artefatos preservados em TEMP:
- hor158-main-baseline.log; hor158-main-pytest/
- hor158-main-repeat-1.log até hor158-main-repeat-30.log, e seus basetemps
- hor158-branch-verify.log; hor158-branch-verify-pytest/
