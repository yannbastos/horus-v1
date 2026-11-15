# Notificações internas — backend (HOR-098)

Histórico **derivado** das tabelas existentes (`jobs`, `productions`, `scripts`, `workflows`); nenhuma tabela nem migração nova. Só o estado lido/não lido é gravado, em `notifications-state.json` na pasta de dados (troca atômica, tolerante a arquivo corrompido: o aviso vai ao log, o arquivo é recriado vazio e a API continua). A interface fica para depois; as rotas abaixo são aditivas e autenticadas como as demais.

| Rota | Função |
|---|---|
| `GET /v1/notifications?limit=50&after=<cursor>&unread=true\|false&kind=<tipo>` | `{items, unread_count, next_after}`; ordem por `at` decrescente; cursor opaco |
| `GET /v1/notifications/unread-count` | `{unread_count}` leve, para consulta periódica |
| `POST /v1/notifications/read-state` | `{"ids": [≤200]}` ou `{"all": true}`, mais `"read": true\|false`; idempotente; devolve `{unread_count}` |

Item: `id` estável, `kind` (`production.succeeded|failed|interrupted`, `script.awaiting_review`, `workflow.awaiting_approval`, `workflow.awaiting_authorization`), `severity` (`success|error|warning|info`), `title`, `message`, `at`, `ref` (`{kind, id}`), `pending`, `read`. Produções: janela de 30 dias; aprovações/autorizações pendentes aparecem enquanto aguardam (o banco não guarda o histórico delas). Mensagens só trazem rótulos e identificadores curtos; de uma falha entra apenas o código estável do diagnóstico, nunca texto livre de erro, caminho ou conteúdo de roteiro. Marcações de leitura com mais de 90 dias são podadas (máx. 5000).
