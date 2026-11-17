# HOR-164 (M2) — Relatório de Implementação Frontend (Supabase PostgreSQL)

Data: 2026-11-17
Branch: `yannbastos/sb-m2-frontend`
Commit: `d803ed1`

## 1. Levantamento de dependências (Tarefa 1)
Relatório completo salvo em `C:\Users\yannn\AppData\Local\Horus\dev-reports\auditoria-contexto-2026-11-17\supabase\M2-frontend-dependencias.md`.
Mapeadas todas as referências a armazenamento local/SQLite em `frontend/src/**` (`DiskEncryptionCard.tsx`, `DataDeletionDialog.tsx`, `DataDeletionHistorySection.tsx`, `ProductionRun.tsx`, `PrivacySecurity.tsx`, `Settings.tsx`, `UpdateSection.tsx`, `ImportedProjectsSection.tsx`, `ReadinessPanel.tsx`, `App.tsx`), endpoints `/v1/*` e specs Playwright dependentes.

## 2. Implementação contra o contrato congelado M1 v2 (Tarefa 2)

### Arquivos criados/alterados
- `frontend/src/services/database.ts`: cliente e parser para `GET /v1/database/status`, `POST /v1/database/connection`, `DELETE /v1/database/connection` e `POST /v1/database/test`; suporte aos 8 estados (`not_configured`, `connecting`, `connected`, `unreachable`, `auth_failed`, `schema_mismatch`, `migrating`, `vault_unavailable`); tratamento do envelope `{detail:{code, message, status}}` e códigos estáveis (`database_vault_unavailable`, `database_auth_failed`, `database_unreachable`, `database_dns_failed`, `database_tls_invalid`, `database_role_invalid`, `database_schema_mismatch`, `database_busy`, `database_invalid_connection`, `database_endpoint_not_allowed`, `database_internal_error`); degradação graciosa para HTTP 404/405/501 ou payload legado.
- `frontend/src/services/api.ts`: extração de `code`/`message` em respostas de erro e notificação reativa para erros de banco ocorridos durante ações em andamento.
- `frontend/src/components/DatabaseSettingsSection.tsx`: nova seção **Ajustes → Banco de dados** com status (`role="status"`), rótulo de host, latência, esquema encontrado × esperado, botão **Testar conexão**, **Remover credenciais**, dicas por código de erro e formulário seguro (senha mascarada, `autoComplete="off"`, limpeza imediata do estado React antes do envio, validação de limites sem trim na senha, tratamento separado de HTTP 401/403 local e HTTP 409/422/503).
- `frontend/src/components/DatabaseGateNotice.tsx`: faixa global discreta e cartão de bloqueio/orientação com ações de retry e navegação direta para **Ajustes → Banco de dados**.
- `frontend/src/features/Settings.tsx`: adição da aba `BANCO DE DADOS` (`GERAL`, `PRIVACIDADE E SEGURANÇA`, `BANCO DE DADOS`) com navegação por teclado (`ArrowLeft`, `ArrowRight`, `Home`, `End`).
- `frontend/src/App.tsx`, `frontend/src/components/Sections.tsx`, `frontend/src/features/EditorialWorkflows.tsx`: consulta inicial e polling para estados transitórios (`connecting`/`migrating`), faixa global quando não `connected`, orientação de primeiro uso (`not_configured`) e bloqueio explícito sem destruir formulários abertos (permitindo retry quando a conexão retorna).
- `frontend/tests/database-connection.spec.ts`: 7 testes Playwright cobrindo os 8 estados, primeiro uso, envio de senha com caracteres especiais sem vazamento no DOM/storage/console/URL, exclusão idempotente, contrato HTTP 200-diagnóstico vs 409/422/503/401, queda no meio de ação com preservação de formulário e retry, navegação por teclado/axe-core e degradação graciosa em backend legado (404).
- `frontend/tests/privacy-security-polish.spec.ts` e `frontend/tests/workspace.spec.ts`: ajustados para a 3ª aba em Ajustes e para a chamada `GET /v1/database/status` na inicialização autenticada.

## 3. Verificações executadas
- `npm --prefix frontend run build`: aprovado (`tsc --noEmit && vite build`, 99 módulos).
- `HORUS_FRONTEND_PORT=1431 npm --prefix frontend test -- tests/database-connection.spec.ts`: **7 passed**.
- `HORUS_FRONTEND_PORT=1431 npm --prefix frontend test -- tests/database-connection.spec.ts tests/privacy-security-polish.spec.ts tests/workspace.spec.ts`: **43 passed**.
- `HORUS_FRONTEND_PORT=1431 npm --prefix frontend test` (suíte completa após última edição): **303 passed, 4 skipped, 1 failed** (a única falha foi `tests/privacy-security-null-regression.spec.ts:426`, que exige `backend/.venv/Scripts/python.exe` ausente neste worktree isolado de frontend).

## 4. Pendências dependentes do backend (M1)
- Integração real com os endpoints congelados `/v1/database/{status,connection,test}` e validação E2E em ambiente com `backend/.venv` e PostgreSQL/keyring ativos.
- Atualização futura de textos em `DataDeletionDialog.tsx` e `DiskEncryptionCard.tsx` quando o backend remover o backup/estado SQLite legado desses fluxos.
