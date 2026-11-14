# Migração do legado C#/Avalonia — matriz e critérios de aceite (HOR-010)

Levantamento de 14/11/2026 (Claude Sonnet) sobre a branch `docs-hor-010-legacy-matrix`. Este documento **não autoriza remover** o C#: a remoção é executada pelo Maestro após o aceite abaixo (a [governança](AGENT-GOVERNANCE.md) e o [AGENTS.md](../AGENTS.md) mantêm o Avalonia até a equivalência ser aceita). Estado operacional permanece na Issue HOR-010 (#12 de `yannbastos/horus-management`).

## 1. Inventário do legado

| Item | Tamanho | Observação |
|---|---|---|
| `src/Horus` (Avalonia, 12.1.4) | ~1,3 mil linhas de código próprio (+ 2 fontes TTF e 2 licenças OFL) | 3 serviços, 2 ViewModels, 4 views, tema |
| `tests/Horus.Tests` (xUnit v3 + Avalonia.Headless) | 8 arquivos, ~800 linhas | 1 teste de rede (`Category=Network`) |
| `Horus.slnx`, `global.json` (SDK 10.0.100), `Directory.Build.props` | 3 arquivos | só servem ao C# |
| `.editorconfig` (seção `[*.cs]`), `.gitignore` (bloco Avalonia) | 2 trechos | resíduo C# |
| `frontend/src-tauri/src/main.rs` — comando `open_legacy`; `frontend/src/platform/desktop.ts` — `openLegacy()` | ~15 linhas | `openLegacy` **não é chamado** em nenhuma tela (confirmado por busca); `HORUS_LEGACY_APP` e `DOTNET_ROOT` só existem para ele |
| `.github/**` | — | **não existe** neste repositório: não há CI a atualizar |
| `scripts/` | — | nenhuma referência a .NET |
| `../fabrica-canais` | — | nenhuma referência a C#/Avalonia |
| `../horus-storybook` | — | só texto: README, AGENTS e CARTA-AO-CODEX citam o Avalonia como referência visual (atualizar depois) |

Referências textuais a Avalonia/.NET (a atualizar na remoção, ver §4): `README.md` (≈20 trechos, incluindo instalação do SDK e comandos `dotnet`), `AGENTS.md` (3), `docs/ARCHITECTURE.md` (§ "Desktop Avalonia preservado", tabela de componentes, árvore de pastas, tokens), `docs/ROADMAP.md`, `docs/migracao-local.md`, `docs/CONTINUAR-EM-OUTRO-PC.md`, `docs/inventario-fluxos-dados.md`, `docs/layout-desktop.md`, comentários em `backend/src/horus_backend/{jobs,transcriber}.py` e `frontend/src/{components/Identity,features/Transcription}.tsx`. `docs/planejamento/**`, `docs/historico/**` e relatórios datados são histórico e **não** são reescritos.

## 2. Matriz funcionalidade → equivalente atual → ação

| Funcionalidade legada (`src/Horus`) | Equivalente atual | Ação |
|---|---|---|
| Fila de até 20 arquivos, FIFO, um por vez (`TranscriptionViewModel`) | `frontend/src/features/Transcription.tsx` (`maxQueue = 20`); `frontend/tests/transcription-parity.spec.ts` | já existe |
| Pausa ao concluir; Próximo só após salvar a revisão atual; editar exige novo salvamento | `Transcription.tsx`; `transcription-parity.spec.ts`, `workspace.spec.ts` | já existe |
| Texto parcial preservado em cancelamento/falha; retry no mesmo arquivo | `backend/src/horus_backend/jobs.py`, `transcriber.py`; `backend/tests/test_transcription.py` (cancel, failure, checkpoint) e `transcription-recovery.spec.ts` | já existe (mais robusto: checkpoints persistentes) |
| Salvar `.txt` com erro acionável; copiar para a área de transferência | endpoint de salvar (somente `.txt`); `test_transcription.py::test_save_writes_only_txt`; `transcription-parity.spec.ts` | já existe |
| Transcrição com Whisper.net (ggml), modelos Tiny…Large v3 Turbo, idiomas auto/pt/en/es | `transcriber.py` (faster-whisper, CPU int8 / CUDA com fallback único); lista de modelos/idiomas: `test_legacy_parity_transcriber.py` | já existe |
| Conversão para WAV 16 kHz mono via ffmpeg (`AudioConverter`) | faster-whisper decodifica a mídia diretamente (PyAV); ffmpeg é usado em download, miniaturas e piloto (`dependencies.find_tool`) | descartar: etapa desnecessária na stack Python |
| Download de modelos ggml em `%LOCALAPPDATA%/Horus/models` | modelos HF em `HORUS_DATA_DIR/models` (baixa de novo no 1º uso) | descartar: formatos diferentes; não migrar arquivos |
| Download YouTube: validação de URL (vídeo único, HTTPS, sem credenciais) | `transcriber.is_youtube_url`; `test_transcription.py::test_youtube_url_rule` + `test_legacy_parity_transcriber.py` (host falso, `file:`, injeção, `live`/`embed`/`music`) | já existe (agora com cobertura equivalente) |
| Download YouTube: argumentos seguros e `HORUS_FILE:` | `transcriber.download`; `test_devices_dependencies.py` (ffmpeg/node, TLS, fallback sem shell) + `test_legacy_parity_transcriber.py` | já existe |
| Localização de ffmpeg/yt-dlp ao lado do exe, `%LOCALAPPDATA%/Horus/tools`, PATH | `dependencies.find_tool` (configuração explícita, PATH, extras); `test_devices_dependencies.py` | já existe (`tools` do Avalonia: descartar, não copiar da instalação antiga) |
| Navegação (Visão geral, Radar, Biblioteca, Produção, Canais, Histórico, Ajustes, Transcrição) e prévias “em preparação” | `frontend/src/App.tsx` + `features/*` com backend real; `workspace.spec.ts`, `production-*.spec.ts` | já existe (prévias simuladas não são transportadas) |
| Temas claro/escuro/sistema; janela de referência 1320×840, mínimo 1040×680 | `frontend/src/styles/tokens.css`, `Settings.tsx`; `workspace.spec.ts` (temas, 1040, sem rolagem horizontal), `visual-capture.spec.ts` | já existe |
| Tokens e fontes (Archivo Expanded, Geist Mono, Inter) | `tokens.css`; `frontend/public/fonts/*` (**SHA-256 idêntico** ao `src/Horus/Assets/Fonts`, licenças OFL incluídas) | já existe |
| Contraste de texto sobre superfícies (`ThemeTests`) | nenhuma verificação automática | **migrar** (lacuna menor, ver §3 e §5) |
| Botão Abrir app legado (`open_legacy`, `HORUS_LEGACY_APP`) | — | descartar: ponte para o app que será removido, sem chamadores |

Nenhuma lacuna exige contrato novo entre front e back; nenhum endpoint foi proposto.

## 3. Dependências que não devem ser transportadas

| Dependência C# | Equivalente atual |
|---|---|
| Avalonia, Avalonia.Desktop, Fonts.Inter, Themes.Fluent | React + Vite + Tauri 2 (`frontend/`, `frontend/src-tauri`); Inter via tokens CSS |
| CommunityToolkit.Mvvm | estado React nos componentes e serviços (`frontend/src/services`) |
| Whisper.net + Runtime + Runtime.Cuda.Windows (modelos ggml) | faster-whisper/CTranslate2 no extra `transcription` do backend; CUDA→CPU em `transcriber.choose_device` |
| `AudioConverter` (ffmpeg → WAV) | decodificação nativa do faster-whisper; `find_tool('ffmpeg')` para o restante |
| xUnit v3, Avalonia.Headless.XUnit, Microsoft.NET.Test.Sdk | pytest (`backend/tests`) e Playwright (`frontend/tests`) |
| SDK .NET 10 (`global.json`) | uv/Python, Node, Rust/Cargo |

## 4. Mapeamento dos testes C# (≈ 60 casos)

| Teste C# | Cobertura atual | Situação |
|---|---|---|
| `TranscriptionViewModelTests` — limite da fila 0/1/20/21, ordem, adicionar sem substituir atual | `transcription-parity.spec.ts` (seleção múltipla, limite 20, remoção) | coberto |
| …conclusão pausa; salvar libera avanço FIFO; editar após salvar re-bloqueia; salvar cancelado/falho bloqueia; salvar durante edição | `transcription-parity.spec.ts`, `workspace.spec.ts` | coberto |
| …falha/cancelamento preserva parcial e retry mantém o arquivo; cancelar vence retorno normal | `test_transcription.py` (`cancel_preserves_partial`, `cancel_wins_even_when_worker_returns_success`, `failure_reports_worker_reason`), `transcription-recovery.spec.ts` | coberto |
| …download entra no fim da fila; fila cheia desabilita download; URL válida e ocioso | `transcription-parity.spec.ts` (download na fila, limite 20); URL: `test_youtube_url_rule` | coberto |
| …silêncio salva texto vazio; erro ao salvar (picker/diretório) acionável | `test_empty_transcription_succeeds…`, `transcription-parity.spec.ts` | coberto |
| `YouTubeDownloadTests.DownloadUsesSafeArguments…` | `test_legacy_parity_transcriber.py::test_download_arguments_are_safe…` (novo) + `test_devices_dependencies.py` | coberto |
| `YouTubeDownloadTests.ValidatesSingleYouTubeVideo` | `test_legacy_parity_transcriber.py::test_only_a_single_https_youtube_video…` (novo, 18 casos) | coberto |
| `YouTubeIntegrationTests` (rede real) | — | descartar: exige rede/yt-dlp reais; o mesmo caminho é validado com mocks |
| `MainWindowTests` (título/“em desenvolvimento”) | — | descartar: texto do scaffold removido |
| `TranscriptionViewTests` (visão geral inicial, fila na tela, transcrição só-leitura ao ocupar) | `workspace.spec.ts`, `transcription-parity.spec.ts` | coberto (campo só-leitura ocupado: parcial, mesmo spec) |
| `ExampleScreensTests` navegação (8 telas) | `workspace.spec.ts`, specs de produção/ajustes | coberto |
| `ExampleScreensTests` tema claro/escuro/sistema | `workspace.spec.ts` (claro/escuro e persistência); **sistema sem teste** | sem cobertura (parcial) |
| `WorkspaceLayoutTests` — ações da visão geral abrem a seção; preserva transcrição entre seções; ação primária dentro da janela 1040/1320 × claro/escuro | `workspace.spec.ts`, `visual-capture.spec.ts` | coberto |
| `ThemeTests` — contraste texto/superfície, botão primário legível, fontes carregam, fundo segue tema | fundo e fontes: `workspace.spec.ts`/`visual-capture.spec.ts` (indiretos); **contraste: sem cobertura** | sem cobertura (contraste, tema “sistema”, carregamento de fontes) |

Lacunas remanescentes (para o Antigravity, em `frontend/tests`, fora do escopo desta tarefa): teste de contraste dos tokens por tema, teste do tema “sistema” (`prefers-color-scheme`) e verificação de que as fontes de `frontend/public/fonts` carregam. Não bloqueiam a remoção se o Maestro aceitá-las como dívida registrada; bloqueiam se o aceite exigir paridade visual completa.

## 5. Plano de remoção (Maestro, após o aceite)

1. **Remover:** `src/` (inclui fontes duplicadas — já idênticas em `frontend/public/fonts`), `tests/`, `Horus.slnx`, `global.json`, `Directory.Build.props`, seção `[*.cs]` do `.editorconfig` e bloco Avalonia do `.gitignore`.
2. **Rust/React (delegar ao Antigravity/Codex):** apagar `open_legacy` e o `DOTNET_ROOT` em `frontend/src-tauri/src/main.rs` (e do `invoke_handler`), `openLegacy` em `frontend/src/platform/desktop.ts`, e a menção a `HORUS_LEGACY_APP` na documentação; ajustar os comentários que citam o Avalonia (`jobs.py`, `transcriber.py`, `Identity.tsx`, `Transcription.tsx`) para a regra própria.
3. **Docs vigentes:** `README.md` (remover instalação do .NET, comandos `dotnet` e a seção do desktop Avalonia; capa e captura); `AGENTS.md` (comandos `dotnet`, “Avalonia até equivalência aceita” → registrar a equivalência aceita; `src/Horus/Theme/Tokens.axaml`); `docs/ARCHITECTURE.md` (tabela “Desktop preservado”, árvore, seção “Desktop Avalonia preservado”, tokens/movimento “Referência Avalonia”); `docs/ROADMAP.md`; `docs/CONTINUAR-EM-OUTRO-PC.md` (pré-requisito .NET); `docs/migracao-local.md`, `docs/inventario-fluxos-dados.md`, `docs/layout-desktop.md` (marcar histórico); `../horus-storybook` (README/AGENTS/CARTA: referência ao Avalonia). Não editar `docs/planejamento/**`, `docs/historico/**` e relatórios.
4. **CI:** não há `.github/` no repositório; nada a ajustar. Se um CI for criado, não incluir `dotnet`.
5. **Preservar:** a decisão registrada no commit/Issue de que as fontes OFL continuam em `frontend/public/fonts` com as licenças.

## 6. Critérios de aceite para fechar HOR-010

1. Esta matriz revisada pelo Maestro, sem linha “migrar” aberta ou com cada lacuna da §4 registrada como dívida em Issue própria.
2. `backend/tests/test_legacy_parity_transcriber.py` e a suíte de transcrição passam: `pytest backend/tests/test_legacy_parity_transcriber.py backend/tests/test_transcription.py backend/tests/test_devices_dependencies.py`; `npx playwright test transcription-parity workspace` verde.
3. Fluxo manual no app Tauri (sem o C#): transcrever, cancelar, salvar `.txt` e avançar a fila; baixar vídeo do YouTube com autorização do usuário (rede real, fora da automação).
4. Após a remoção: `git grep -iE "avalonia|dotnet|\.csproj|Horus\.slnx|HORUS_LEGACY_APP"` retorna apenas docs históricas (`docs/planejamento`, `docs/historico`, relatórios datados).
5. `npm --prefix frontend run build`, `npm --prefix frontend test` e `pytest backend/tests -q` aprovados; `cargo check` em `frontend/src-tauri` aprovado.
6. Nenhum arquivo em `src/`, `tests/`, `*.sln*`, `*.csproj` no repositório; Issue HOR-010 atualizada com evidências e Status Concluído.
