# Checkpoints da retomada — novembro de 2026

Histórico datado de 14–16/11/2026, arquivado em 17/11/2026. **NÃO é instrução vigente.** O estado atual está no [Project](https://github.com/users/yannbastos/projects/1) e nas [Issues de gestão](https://github.com/yannbastos/horus-management/issues). Consulte somente para investigar uma pendência concreta.

O bloco abaixo preserva integralmente, na ordem e sem editar, F.1 e G–K da antiga [RETOMADA-MAESTRO](../RETOMADA-MAESTRO.md). É fonte Markdown literal: comandos, autorizações, preferências e links internos são registros históricos, não passos a executar. Os caminhos dentro do bloco eram relativos a `docs/` (ou à raiz, quando indicados).

Para consultar as fontes atuais citadas no bloco: [piloto local](../piloto-local.md), [governança](../AGENT-GOVERNANCE.md) e [ADR do atualizador](../adr-atualizador.md).

````markdown
### F.1 Cadeia retida da linha Hermes (estado de 14/11/2026)

**Atualização de 14/11 (noite): integre somente `yannbastos/integ-hermes-line-v3`**, que já consolida toda a tabela abaixo mais `test-hor-130-adversarial` e `yannbastos/feat-hor-107-checkpoints-ui` sobre a `main` daquele dia (backend+contracts 2490 passed; migração v9→v12 em cópia ok; Playwright 236+4; jornada real só o teste 1 por falta de memória — repetir 3/3 + Final na integração). Antes de integrar, faça `git merge origin/main` na v3 para trazer a `main` atual. A tabela e o procedimento abaixo ficam como referência da composição.

Todas as branches estão publicadas em `origin` e **nenhuma está na `main`**. Cada uma contém a anterior; migrações aditivas v10–v12.

| Ordem | Branch | O que acrescenta | Migração | Issue de gestão |
|---|---|---|---|---|
| 1 | `yannbastos/integ-hermes-line-v2` | etapa `script` do Hermes (HOR-134) + conciliação + auditoria append-only de transmissões (HOR-115), já integradas entre si | v10 (`scripts.generator`), v11 (`transmissions`) | #121, #103 |
| 2 | `feat-hor-111-egress-gateway` | gateway único de saída HTTP, deny by default, allowlist (`HORUS_EGRESS_ALLOW`), anti-rebinding, auditoria; allowlist de download derivada de `is_youtube_url` | — | #99 |
| 3 | `feat-hor-107-human-checkpoints` | checkpoints humanos para gastar e publicar, com barreira no backend | v12 (`human_checkpoints`) | #95 |
| paralela | `yannbastos/feat-hor-115-transmissions-ui` | tela de transmissões (frontend); parte de `integ-hermes-line-v2`, **não** depende de 2 e 3 | — | #103 |

As branches originais `feat-hor-134-script-stage`, `fix-hor-134-script-stage-review` e `feat-hor-115-transmission-audit` já estão contidas na linha (não integre separadamente). A `integ-hermes-line` (v1) é anterior e foi substituída pela v2.

**Contrato da API:** todo o aumento é aditivo e o snapshot (`backend/tests/snapshots/openapi.json`) já está atualizado nas branches. **Aprovado** para a integração desta linha após o teste do primeiro vídeo: `HermesScriptRequest`, `GET /v1/security/transmissions`, `POST /v1/workflows/{id}/scripts/reconcile` (ordem 1) e `POST/GET /v1/checkpoints` (ordem 3). Diferença extra no snapshot ao integrar = parar e avisar.

**Quando integrar:** somente depois de o usuário testar o primeiro vídeo na `main` atual (o fluxo não deve mudar até lá), em lote, com a suíte completa uma única vez. Não use dados reais do usuário: `HORUS_DATA_DIR` temporário.

**Procedimento e validação (a partir de `origin/main` atualizada; merge normal, sem rebase nem force):**

```powershell
git fetch origin
git switch -c integ-linha-hermes origin/main
git merge --no-ff origin/yannbastos/integ-hermes-line-v2      # 1; conflitos esperados em api.py (imports/handlers): manter o que a main já tem
git merge --no-ff origin/feat-hor-111-egress-gateway          # 2
git merge --no-ff origin/feat-hor-107-human-checkpoints       # 3
uv sync --project backend --frozen --extra test
& backend/.venv/Scripts/python.exe -m pytest backend/tests contracts/hermes/tests -q      # uma vez
$env:HORUS_UPDATE_API_SNAPSHOT = '1'; & backend/.venv/Scripts/python.exe -m pytest backend/tests/test_api_contract_snapshot.py -q; Remove-Item Env:HORUS_UPDATE_API_SNAPSHOT
git diff --stat -- backend/tests/snapshots                    # só linhas adicionadas, nada removido
```

Conflitos prováveis ao integrar na `main` de hoje: `backend/src/horus_backend/api.py` (imports e handlers de `script_call`), testes que fixam `user_version` (já usam `SCHEMA_VERSION` nas branches) e a allowlist de `test_network_inventory.py` (o gateway acrescenta F-20). Depois da suíte:

1. **Migração v9→v12 em CÓPIA** de um banco de teste (nunca o real): criar o banco na v9, copiar, abrir a cópia com `horus_backend.migrations.migrate`, conferir `PRAGMA user_version = 12`, integridade, o backup automático `horus.backup-v9-*` (verificável com `python -m horus_backend.backups verify`) e que reabrir é idempotente. Antes de migrar o banco real do usuário, gerar o pacote verificável: `python -m horus_backend.local_data_export export <pasta-de-dados> --to <pacote.zip>` e `verify`.
2. Frontend da ordem paralela (`feat-hor-115-transmissions-ui`): `npm --prefix frontend ci --ignore-scripts`, `npm --prefix frontend run build`, `npm --prefix frontend test` (Playwright). Integrar por último, com a identidade visual preservada.
3. Jornada real opcional (`node frontend/scripts/e2e-backend.mjs run`, variáveis em [piloto-local.md](piloto-local.md)); com a máquina carregada os tempos sobem ~2,5× e a auditoria de rede do render já espera até 30 s pelo NetLog.
4. Atualizar as Issues #121, #103, #99 e #95 (evidência + Status) e mover para Concluído.

Não ativar inferência real, provedores pagos nem publicação ao integrar: o gate de segurança e os checkpoints humanos continuam bloqueando por padrão.

Siga a seção "Eficiência de consumo" de [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md).

## G. Ponto de pausa (14/11/2026, noite)

Pausa pedida pelo usuário. `main` publicada e validada (backend completo 2124 passed em e5d7c17; jornada real 3/3 sobre a produção no app instalado; Playwright completo 234/235 com a única falha corrigida em seguida). Nenhum agente em execução.

- Aguardando o usuário: teste humano do primeiro vídeo (guia completo, inclusive prévia Kokoro); autorização específica para integrar `integ-hermes-line-v3` (§F.1); decisão sobre download automático do modelo Whisper; exclusão de dados locais (desenho em docs/seguranca-dados.md).
- Retomar: revisão independente de docs/avaliacao-provedores-hermes.md (Issue #104, branch `review-hor-116-providers` se existir); variante Final da jornada (HORUS_E2E_FINAL=1) a repetir na `main` atual; UI das notificações e da importação já estão na `main`.
- Regras de despacho: um Codex por vez (pedido do usuário); Antigravity sempre em worker novo com `--model gemini-4-hydrogen-medium`; confirmar `activity=working` após cada despacho.

## H. Checkpoint — modo econômico de Claude (15/11/2026)

Pedido do usuário: até nova orientação, Opus só coordena decisões críticas; Sonnet só para revisão de segurança que outro agente não faça; trabalho vai para Codex e Antigravity com Gemini 4 Hydrogen (nunca Argon nem Claude/GPT no Antigravity). Conferir cotas no Orca antes de delegar. Resumos de até 150 palavras.

- **Persistência:** ADR aceito (docs/adr-persistencia-local-remota.md): SQLite local continua fonte; Supabase `horus-dev` (ref `uzirimqmwohnfraszvjn`, CLI via `npx supabase`, login feito pelo usuário) só para metadados sincronizáveis por adesão; acesso pela opção B. Issues: #124 (Fase A, Em revisão), #126 Fase B (Codex em `feat-hor-139-phase-b`), #127 C, #128 D, #129 E (bloqueada até aprovação do usuário). Contrato remoto da Fase B precisa de revisão de segurança antes da implementação dependente.
- **Em andamento:** Codex #126; Antigravity UI de exclusão segura (#105, `feat-hor-117-delete-ui`); Sonnet encerrando em ponto seguro `feat-hor-120-secrets-hardening` (#4, 7.2–7.4 em simulação).
- **Fila curta:** Codex → backend do download explícito do Whisper (#125; UI já na main, oculta até o catálogo existir) → restante de 7.2–7.4 (#4) se o Sonnet não concluir. Gemini → reconciliação periódica do Project → stories/docs dos componentes novos → UX da Fase D quando a B integrar.
- **Aguardam o usuário:** teste do primeiro vídeo; integração da `integ-hermes-line-v3`; limpeza das 210 entradas do cofre (dizer qual pasta de dados proteger); aplicação real de cofre/ACL/rotação; senha do banco se `supabase link/db push` exigir (rodar no terminal dele).

## I. Troca de computador (16/11/2026) — estado para retomar

Programação pausada a pedido do usuário; nenhum agente ativo. Abra a sessão na raiz de `horus` e rode `/horus-maestro`. Se a sessão for aberta na pasta-mãe imediata, rode antes (idempotente, sem caminhos pessoais): `powershell -NoProfile -ExecutionPolicy Bypass -File horus/scripts/install-workspace-skill-bridge.ps1`. Se a sessão abrir em uma pasta ancestral acima da pasta-mãe (ex.: workspace raiz do Orca), informe `-WorkspacePath` (ex.: `powershell -NoProfile -ExecutionPolicy Bypass -File HORUS/horus/scripts/install-workspace-skill-bridge.ps1 -WorkspacePath .`). O Claude Code também lista a skill versionada como `horus:horus-maestro` quando arquivos de `horus/` entram no contexto.

- **Prioridade 1 — #130 (HOR-143), primeiro vídeo no app instalado:** correções integradas na `main` (roteiro manual sem proposta via `POST /v1/workflows/manual`, guia leva ao editor, fábrica legada em seção avançada, perfil offline do instalador). Próximo passo: Codex gerar o instalador 0.1.1 (perfil offline, ~1,9 GB; `docs/empacotamento-windows.md`), instalar com `HORUS_DATA_DIR` temporário e executar a jornada real até MP4/QC/licenças/revisão; entregar caminho, SHA-256 e instruções ao usuário. Sem release pública.
- **Integradas em 16/11 (ver §J):** PR #3 (#125 backend do download explícito do Whisper), PR #4 (#126 Fase B do Supabase, schema privado aplicado no `horus-dev` com RLS provada) e branch `yannbastos/feat-hor-117-delete-ui` (#105 UI de exclusão; story em `horus-storybook` `feat-hor-117-delete-ui`).
- **Retidas:** `yannbastos/integ-hermes-line-v3` (Hermes; só com autorização específica); Fase E do Supabase (#129).
- **Aguardam o usuário:** teste do primeiro vídeo no novo instalador; pasta a proteger antes da limpeza do cofre (210 entradas); aplicação real de cofre do token do Hermes, ACL e rotação (`docs/seguranca-dados.md`); integração Hermes.
- **Modo econômico de Claude (substituído por §J em 16/11 tarde):** Opus só coordena; Sonnet só revisão de segurança; trabalho em Codex (um por vez) e Antigravity com `gemini-4-hydrogen-medium`. Integração sempre roda os testes transversais (`test_network_inventory`, snapshots, `test_supply_chain`, `test_endpoint_auth*`).

## J. Checkpoint de 16/11/2026 (tarde)

- **Prioridade de consumo (pedido do usuário; substitui §H/§I onde conflitar):** Maestro = Claude Fable 5.2, exclusivamente para coordenação, revisão e integração; implementação, investigação e testes vão primeiro para **Antigravity/Gemini** (`gemini-4-hydrogen-medium`) e **Codex** (liberado); **Claude Sonnet só quando necessário**; **não usar Claude Opus**. Motivo: cota Claude em 53% em 16/11.
- **Estado:** `main` em `9591347` + este commit. Integrados no dia: lote PR #3/#4 + UI de exclusão, HOR-144 (ponte da skill, atualizador de CLIs, `atualizar-ias`, bloqueio de downgrade), HOR-145 (ambiente dev; `desktop:dev` usa `tauri.dev.conf.json`), HOR-146 (UX) e regra de encerramento. Issues concluídas no dia: HOR-041, 137, 138, 139, 145, 146. Em revisão: HOR-117 (#105: faltam quarentena, escopos Radar/transcrições/tudo, exclusão remota e aviso de retenção externa), HOR-144 (#131: aguarda o usuário validar `/horus-maestro` em sessão nova), HOR-100 (#88: ADR do atualizador, aguardando decisão do canal).
- **Ambiente deste PC:** ferramentas de produção em `%LOCALAPPDATA%\Horus\dev-tools` (FFmpeg, Kokoro, Whisper small) e variáveis `HORUS_FFMPEG`, `HORUS_FFPROBE`, `HORUS_KOKORO_PYTHON`, `HORUS_KOKORO_MODEL_DIR`, `HORUS_ALIGNMENT_MODEL`, `HORUS_WHISPER_MODELS_DIR` e `FABRICA_HOME` a passar ao `npm --prefix frontend run desktop:dev`; o Hórus deve ser aberto como app desktop (Tauri), não no navegador.
- **Aguardam o usuário:** canal do atualizador; exclusão das 6 branches-snapshot (`review/*`, `feat-hor-049`, `feat-hor-052`); destino de `checkpoint-2026-11-16`; `integ-hermes-line-v3` (126 commits atrás, 8 conflitos; só com autorização); 4 propostas de UX da HOR-146; teste humano do primeiro vídeo.

## K. Checkpoint de 16/11/2026 (fim da tarde) — HOR-100

- **Estado:** `main` em `ec3a3b8` + este commit. HOR-100 (#88) com as três etapas do protótipo local integradas: dados fora da instalação e backup (etapa 1), plugin updater com guardas (etapa 2), ciclo instalado N→N+1 com saúde pós-reinício (etapa 3, `scripts/test-updater-e2e.ps1`). Updater segue desabilitado no build padrão.
- **Critérios de segurança S1–S6** (pedido do usuário, registrados na #88): bloqueio confirmado pelo backend, backup WAL com restauração validada, sem downgrade automático, sem operações concorrentes, assinatura inválida nunca instalada, sucesso só após saúde pós-reinício (`GET /v1/update/health`). Cobertos em pytest, `cargo test`, `frontend/tests/updater-security.spec.ts` e no ensaio instalado. Política binário × dados em [adr-atualizador.md](adr-atualizador.md).
- **Pitfall do ensaio instalado:** o script compara as pastas reais (`%LOCALAPPDATA%\com.horus.audiovisual`) antes e depois e aborta se variarem. O app de desenvolvimento (`desktop:dev`) usa essa mesma pasta: feche-o antes de rodar o ensaio.
- **#88 fica Em revisão** até as decisões do usuário: caixa "apagar dados do aplicativo" no desinstalador NSIS, canal privado, custódia da chave, Authenticode, retenção/abrangência do backup. Não homologado: Windows limpo, queda de energia, canal remoto. Sem hospedagem nem distribuição pública.
- **Limpeza:** worktrees e branches `feat-hor-100-*` integradas foram removidas (local e remoto).
````
