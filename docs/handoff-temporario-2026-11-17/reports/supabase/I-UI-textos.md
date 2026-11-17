# HOR-164 (I-UI) — Textos da interface para PostgreSQL (Supabase)

Escopo: só frontend (textos e specs). Sem alteração de AGENTS/CLAUDE/governança/backend.

| Arquivo:linha | Antes | Depois |
|---|---|---|
| `frontend/src/components/DataDeletionDialog.tsx:220` | EXCLUSÃO SEGURA DE DADOS LOCAIS | EXCLUSÃO SEGURA DE DADOS |
| `DataDeletionDialog.tsx:275-281` | "o pacote de backup inclui apenas o banco de dados e manifestos JSON" | "export versionado, com manifesto, dos registros do banco PostgreSQL (Supabase)" |
| `DataDeletionDialog.tsx:282-286` (novo) | — | "Sem garantia de zeragem física: remove dados do banco e dos arquivos locais, mas o provedor pode reter cópias (WAL e backups)." |
| `frontend/src/components/DiskEncryptionCard.tsx:7` | "...o banco de dados e a conexão do Hermes na pasta de dados" | banco sai da lista; "o banco de dados fica no Supabase (PostgreSQL), fora deste volume" |
| `frontend/tests/data-deletion.spec.ts:406` | "pacote local de pré-exclusão cobre banco e manifestos" | "pacote de pré-exclusão cobre os registros do banco e o manifesto" |
| `data-deletion.spec.ts:~562` (novas asserções) | — | exige "PostgreSQL (Supabase)" e "Sem garantia de zeragem física" |

Mantidos (corretos): textos de Whisper/TTS/fábrica "offline" (ferramentas de mídia locais, sem relação com banco); `Banco local indisponível.` em mocks de spec (mensagem de teste arbitrária).

Pendências: `DiskEncryptionCard.tsx:43` ("dados locais" = mídia/arquivos; ok). `UpdateSection`/`updates.ts` (backup pré-update) é do HOR-163.
