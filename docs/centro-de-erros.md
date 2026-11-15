# Centro de erros (HOR-095, backend)

Leitura agregada das falhas já registradas, **derivada das tabelas existentes** (`jobs`, `productions`, `workflows`); sem tabela nova, sem migração. A interface fica a cargo do frontend. Contrato aditivo, com snapshot em `backend/tests/snapshots/openapi.json`.

| Rota | Resposta |
|---|---|
| `GET /v1/errors?limit=50&after=<cursor>&stage=&code=&kind=&since=&until=` | `{items, next_after}`; ordem por `at` decrescente; cursor opaco; `limit` 1–200 |
| `GET /v1/errors/summary?since=&until=` | `{total, by_code[{code,count,title,suggested_action_pt}], by_stage, by_kind}` |

- `kind`: `production`, `workflow_step`, `job` (fábrica, transcrição, download) ou `workflow` (análise falha sem etapa falha registrada; evita contagem dupla).
- Item: `id`, `kind`, `status` (`failed|blocked|interrupted`), `code`, `stage`, `at`, `title`, `message_pt`, `suggested_action_pt`, `ref{kind,id,workflow_id}`, `duration_seconds`, `exit_code`.
- `code` vem de `result.failure.code` e só é aceito se estiver no catálogo (`error_center.CATALOG`); código desconhecido vira `unknown_failure`. Sem diagnóstico estruturado, o código é derivado de tipo+status (`job_failed`, `workflow_step_blocked`…).
- **Nunca** devolve `stderr_tail`, `jobs.error`, payload, caminhos ou conteúdo de roteiro; títulos e ações em português vêm do catálogo fixo. Novos códigos de falha devem ser acrescentados ao catálogo.
- Parâmetros inválidos (`kind`, `code`, `stage`, datas, cursor) → 422; autenticação Bearer como nas demais rotas. Janela de leitura: até 5000 jobs falhos mais recentes por consulta.
