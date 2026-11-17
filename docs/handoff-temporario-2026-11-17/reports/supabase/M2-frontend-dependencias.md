# HOR-164 (M2) — Dependências de Armazenamento Local / SQLite no Frontend

Data: 2026-11-17
Escopo: `frontend/src/**` e `frontend/tests/**` (levantamento pré-migração para Supabase PostgreSQL como único banco operacional).

---

## 1. Telas, componentes e textos que pressupõem armazenamento local / SQLite

| Arquivo:linha | O que existe hoje | O que muda com Supabase como único banco |
|---|---|---|
| `frontend/src/components/DiskEncryptionCard.tsx:7,43` | Descreve que o Hórus guarda "roteiros, mídia, comentários, o banco de dados e a conexão do Hermes na pasta de dados" e consulta criptografia do volume local. | Texto: remover menção ao banco de dados estar na pasta local (banco operacional fica no Supabase PostgreSQL; mídia e caches locais continuam no volume). Comportamento/erro: inalterados até decisão do backend sobre `/v1/diagnostics`. |
| `frontend/src/components/DataDeletionDialog.tsx:64,220,267-315,387,453` | Modal de **Exclusão Segura de Dados Locais** (HOR-117): cria pacote de backup que "inclui apenas o banco de dados e..." e move arquivos locais para quarentena recuperável; exibe "O que permanece no banco de dados e por quê". | Texto: esclarecer que os registros de auditoria/metadados permanecem no banco Supabase PostgreSQL enquanto apenas mídia/arquivos locais vão para quarentena (ou adaptar caso backup deixe de incluir SQLite local). Estado de erro: se Supabase estiver indisponível, bloquear execução com erro claro (sem fallback). |
| `frontend/src/components/DataDeletionHistorySection.tsx:14-16,44,152-171,229,269-271` | Histórico de exclusões locais e quarentena; exibe `backup.name` pré-exclusão e permite restaurar da quarentena. | Comportamento: histórico e cadeia de auditoria passam a vir do PostgreSQL; se banco indisponível, exibir erro de conexão/retry sem simular lista vazia. |
| `frontend/src/features/ProductionRun.tsx:1315-1318,2437` | Banner de arquivos excluídos em quarentena e confirmação de movimentação para quarentena recuperável. | Texto/comportamento: continua válido para arquivos de mídia locais, mas ações que dependem do estado persistido no banco devem falhar explicitamente se o banco estiver fora. |
| `frontend/src/services/productionCenter.ts:208` | Mensagem de aviso: "Arquivos locais excluídos (em quarentena). Prévia e exportação indisponíveis até restaurar em Ajustes → Privacidade e Segurança." | Mantém para mídia local; caso o erro seja de conexão com o banco, exibir estado de indisponibilidade do banco. |
| `frontend/src/features/PrivacySecurity.tsx:1-1127` | Aba **Ajustes → Privacidade e Segurança**: consulta `GET /v1/security/status`, `GET /v1/security/transmissions`, `GET /v1/checkpoints`, `DiskEncryptionCard`, `DataDeletionHistorySection`. | Texto: atualizar subtítulo/descrições que assumam banco local; se o banco estiver indisponível, `GET /v1/security/status` ou endpoints dependentes devem reportar erro claro de banco sem mascarar com estado vazio. |
| `frontend/src/features/Settings.tsx:287-297` | Aba **Ajustes → Geral**: "Conexão local" (backend FastAPI local), Hermes, Diagnóstico do ambiente, Whisper, Narração, Atalhos, Atualizações (`UpdateSection`, fora do escopo M2). | Adicionar nova seção **Banco de dados** (`Ajustes → Banco de dados` ou painel dedicado) com status em tempo real, configuração de credenciais (sem segredo no DOM), teste de conexão e remoção de credenciais. |
| `frontend/src/components/UpdateSection.tsx:146-148,408-411,453-454,521-523` | Fluxo de atualização menciona "1. Backup dos dados locais" (`pre-update-*.zip`) e compatibilidade com schema dos dados. *(Escopo de outro worker HOR-163 — não editar aqui).* | Coordenar com HOR-163: backup pré-update não deve depender de SQLite operacional único. |
| `frontend/src/services/updates.ts:34,52,119-120` | Código `backup_failed` e mensagem de backup dos dados locais antes da atualização. *(Escopo de outro worker — não editar).* | Coordenar com HOR-163. |
| `frontend/src/features/library/ImportedProjectsSection.tsx:63-249` | Importação de pacotes `.zip` em modo somente consulta (`POST /v1/imports`, `GET /v1/imports`). | Persistência dos metadados de importação migra para PostgreSQL; arquivos extraídos seguem locais. Se banco indisponível, bloquear importação com erro claro. |
| `frontend/src/features/production/ProductionExportPanel.tsx` | Painel de exportação de produção (`/v1/productions/{id}/exports`, `/export`, `/thumbnail`, `/metadata`). | Metadados/registros de exportação vêm do Supabase; em queda de conexão, manter formulário/estado e permitir repetir. |
| `frontend/src/features/CheckpointsHistory.tsx` e `TransmissionsAudit.tsx` | Histórico de aprovações humanas (`/v1/checkpoints`) e auditoria de transmissões (`/v1/security/transmissions`). | Dados vêm do Supabase; sem conexão, exibir erro claro com opção de tentar novamente. |
| `frontend/src/features/ErrorCenter.tsx` e `services/errorCenter.ts:127,142` | Central de erros (`/v1/errors`, `/v1/errors/summary`). | Consulta ao Supabase; sem conexão, exibir falha de banco em vez de estado vazio. |
| `frontend/src/components/ReadinessPanel.tsx:43,271-272` | Diagnóstico de prontidão local (Whisper, Kokoro, yt-dlp, FFmpeg) via `GET /v1/diagnostics`. | Ferramentas de mídia continuam locais; prontidão geral deve considerar também o estado de conexão com o Supabase (sem bloquear ferramentas locais de diagnóstico). |
| `frontend/src/App.tsx:266-383` | Shell global: barra de avisos (`update`, `productionNotice`), `main` e `footer` ("Backend local conectado"). | Adicionar faixa/aviso global discreto quando o banco estiver `not_configured`, `unreachable`, `auth_failed`, `schema_mismatch` ou `migrating`, além de fluxo guiado no primeiro uso (`not_configured`). |

---

## 2. Chamadas `/v1/...` relacionadas a dados, exportação, backups e segurança

| Endpoint | Serviço em `frontend/src/services/` | Impacto com Supabase |
|---|---|---|
| `GET/POST /v1/data-deletion/preview`, `execute`, `audit`, `{id}/restore` | `dataDeletion.ts:124,139,153,169` | Auditoria e metadados passam a residir no Supabase; erro de banco deve bloquear exclusão/restauração sem fallback local. |
| `GET /v1/security/status`, `GET /v1/security/transmissions` | `api.ts:710,756` | Diagnóstico de segurança e transmissões consulta Supabase; `disk_encryption` continua local. |
| `GET/POST /v1/checkpoints` | `checkpoints.ts:210,219` | Aprovações humanas persistidas no Supabase; erro de banco impede criação/validação de checkpoint. |
| `GET/POST /v1/imports`, `GET /v1/imports/{id}` | `imports.ts:335,366,410` | Registro de pacotes importados no Supabase; falha de conexão deve preservar estado e permitir retry. |
| `GET/POST /v1/productions/{id}/exports`, `export`, `file`, `thumbnail`, `metadata` | `exports.ts:259,285,325,358,393,429` | Histórico de exportações e metadados no Supabase. |
| `GET/POST/PATCH /v1/workflows/*`, `/v1/scripts/*`, `/v1/productions/*`, `/v1/errors*`, `/v1/notifications*` | `api.ts`, `scripts.ts`, `production.ts`, `errorCenter.ts`, `notifications.ts` | Todos dependem do banco operacional; quando o banco estiver indisponível, ações de escrita/leitura devem exibir erro claro e permitir repetição sem fingir persistência. |
| *(Novo)* `GET /v1/database/status`, `POST /v1/database/connection`, `DELETE /v1/database/connection`, `POST /v1/database/test` | *(Novo)* `database.ts` | Novo contrato para conexão e diagnóstico do Supabase PostgreSQL (sem retorno de segredos). |

---

## 3. Testes Playwright que dependem dessas áreas

| Spec em `frontend/tests/` | Dependência | Impacto / Ação |
|---|---|---|
| `data-deletion.spec.ts` | Fluxo de exclusão segura, backup e quarentena (`/v1/data-deletion/*`). | Mantém mocks atuais até o backend atualizar textos/contratos de exclusão; adicionar proteção para erro de banco quando aplicável. |
| `disk-encryption.spec.ts` | `DiskEncryptionCard` via `/v1/diagnostics` e `/v1/security/status`. | Verificar se textos de `DiskEncryptionCard` são asseridos; não quebrar contrato atual antes do backend. |
| `privacy-security-m2.spec.ts`, `privacy-security-null-regression.spec.ts`, `privacy-security-polish.spec.ts` | Aba `Ajustes → Privacidade e Segurança`, `/v1/security/status`, `DataDeletionHistorySection`. | Preservar estrutura da aba `Privacidade e Segurança`; nova seção `Banco de dados` não deve quebrar seletores/tabs existentes. |
| `human-checkpoints.spec.ts`, `transmissions-audit.spec.ts` | `/v1/checkpoints`, `/v1/security/transmissions`. | Continuam funcionando com mocks; se `GET /v1/database/status` retornar 404 (backend antigo), frontend deve degradar graciosamente sem bloquear esses fluxos. |
| `ux-import.spec.ts`, `ux-export.spec.ts` | `/v1/imports`, `/v1/productions/*/exports`. | Mantêm comportamento quando banco conectado ou endpoint `/v1/database/status` ausente (404). |
| `updater-settings.spec.ts`, `hor-148-settings-scroll.spec.ts` | Layout e abas de `Ajustes`. | Garantir que a nova aba/seção `Banco de dados` preserve acessibilidade, teclado e rolagem em `Ajustes`. |
| `first-run-readiness.spec.ts`, `ux-readiness.spec.ts` | `ReadinessPanel` na Visão Geral. | Garantir compatibilidade quando backend não expõe `/v1/database/status` (404) ou quando está `connected`. |
