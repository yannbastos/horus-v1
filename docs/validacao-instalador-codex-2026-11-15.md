# Instalador Windows validado por Codex — 15/11/2026

Escopo HOR-100/HOR-006: gerar um NSIS para o teste humano do primeiro vídeo, sem Claude/Gemini, serviços pagos, inferência Hermes real, migração de dados reais ou release pública.

## Origem e reprodução

Binário construído da `main` **340c04a9ca499492c671564b4fd3fec126d8a5ba**, em checkout isolado `build/hor-100-windows-codex`. Nenhum código da fábrica alterado; PRs de download Whisper e persistência remota ficaram fora deste pacote.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run tauri build -- --config ../scripts/tauri-transcription.json
uv sync --project backend --frozen --extra test --extra measure
```

Backend PyInstaller onedir, Python 3.12 e dependências fixadas por hashes no lock de transcrição. FastAPI, keyring, uvicorn, faster-whisper, CTranslate2, PyAV e tokenizers empacotados. PyInstaller é dependência exclusiva de build. Pesos Whisper/Kokoro, runtime CUDA, fábrica, Python Kokoro, Node/Remotion e FFmpeg ficam externos. O backend empacotado não depende de Python instalado no PATH.

`HORUS_0.1.0_x64-setup.exe`: **76.785.482 bytes**, SHA-256 **cb8466508833fe7e7fdeff364b00f61349581c6e189b6b3ad26d76c5861717a9**. Windows x64, NSIS por usuário, WebView2 preexistente, sem assinatura digital.

## Evidências executadas

- Build real backend/frontend/Tauri/NSIS: aprovado.
- Testes `test_worker_runtime`, `test_diagnostics_production`, `test_production_robustness`, `test_token_rotation`, `test_exports_adversarial`, `test_api_contract_snapshot` e `test_first_video_response_shapes`: **199 passed**, 33,53 s.
- `test-windows-installer.ps1 -WithTranscription`: instalação 9,003 s; abertura com PATH limitado a System32 13,766 s; fechamento normal e queda forçada sem backend/descendentes órfãos; desinstalação 1,315 s. O primeiro ensaio parou por ausência de psutil no runner; o ambiente de testes isolado corrigiu essa preparação.
- Interface Tauri observada: “Backend local conectado”; Whisper, Kokoro e FFmpeg disponíveis no ensaio. Captura local preservada.
- `test-installed-first-video.py`: **exit 0**, `success=true`, `uninstalled=true`, `orphan_processes=[]`. Pacote sem bridge foi corretamente recusado no diagnóstico; após restauração e importação offline do modelo, produção pronta sem reinício.
- Roteiro fictício → Kokoro/pf_dora → Whisper small/alinhamento → fábrica/Remotion → QC → revisão fictícia por hash → exportação. H.264/AAC, **540×960, 30 fps, 21,013 s**; QC **16 pass, 1 warn** (`asset_provenance`), sem erro bloqueante. ZIP 712.244 bytes; SHA-256 **3a891f86b138d5b31a6938429e400ec9375e7946ec693d91887d54d3bce18fbd**; `publishable=false`.
- Inspeção da distribuição: nenhum `.env`, `hermes.json`, banco `.sqlite3`, chave `.pem/.key` ou peso `model.bin` encontrado. O token do desktop continua gerado por sessão; nenhum token incluído no pacote.

## Abertura com recursos locais

`scripts/start-installed-local-pilot.ps1` é um auxiliar externo ao instalador. Recebe um JSON local de caminhos, encontra o desktop instalado pelo registro HKCU e configura somente o ambiente do processo filho. Usa dados separados, executores simulados, provedores pagos desligados e flags offline; não altera configurações globais. `-CheckOnly` valida arquivos sem abrir o app ou criar dados; `-DataDirectory` permite ensaio isolado.

Campos de `local-pilot.json`: `factory`, `ffmpeg`, `kokoro_python`, `kokoro_model_dir`, `whisper_models_dir`, `alignment_models_dir`. O último aponta para a raiz que contém `faster-whisper-small/`, não diretamente para a pasta dos pesos. Não versionar caminhos particulares ou credenciais. O atalho local e seu JSON são artefatos da entrega, não parte do binário NSIS.

Ensaio real adicional do auxiliar: `-CheckOnly` não criou dados; abertura do aplicativo instalado com dados temporários retornou `production.ready=true`, executor simulado e provedor pago desligado; fechamento/desinstalação aprovados, sem órfãos. Um primeiro ensaio identificou a raiz dos modelos incorreta no JSON local; corrigida antes desta validação.

## Limites

Não homologado em VM/Windows limpo. PATH restrito não equivale à ausência de ferramentas, caches, vozes do sistema ou WebView2. Não repetidos nesta entrega: Final 1080×1920, atualização/rollback, migração entre schemas, CUDA e downloads externos. Fábrica preparada no ensaio é externa e estava em pasta temporária preexistente; se removida, deve ser preparada novamente e o JSON local atualizado.

Aceite humano de áudio/vídeo e licenças continua pendente. HOR-100/HOR-006 permanecem abertas por máquina limpa e demais critérios. Evidências completas e artefatos ficam na pasta local `relatorios/INSTALADOR-CODEX-2026-11-15/` do workspace e na Issue privada de gestão; nenhum instalador publicado no GitHub.
