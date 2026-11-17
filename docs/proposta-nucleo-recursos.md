# Proposta arquitetural — Separação entre núcleo e pacote de recursos grandes (HOR-100 / A5)

**Estado:** Estudo técnico e proposta para decisão do usuário (16/11/2026). **Nenhuma alteração de código, empacotamento ou dados foi executada.**  
**Issue:** `yannbastos/horus-management#88` (HOR-100, etapa A5).  
**Documentos relacionados:** [adr-atualizador.md](adr-atualizador.md), [empacotamento-windows.md](empacotamento-windows.md), [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md).

---

## Resumo executivo

Hoje o perfil completo do Hórus (`first-video`) gera um instalador NSIS único de **~680 MB comprimido** e **2.075,25 MiB (2,027 GiB)** instalados em `%LOCALAPPDATA%\Programs\Horus` (medição real somente leitura em disco). Desse total, **1.785,36 MiB (86,0% do espaço instalado)** correspondem ao diretório `backend/first-video/` — composto por `torch` CPU, `spacy`, `transformers`, `node_modules` do Remotion, `ffmpeg.exe`, `ffprobe.exe`, `node.exe` e pesos do Kokoro — que raramente muda entre versões do aplicativo.

Como o `tauri-plugin-updater` no Windows não suporta atualização delta (baixa o instalador NSIS inteiro a cada release), cada correção no frontend ou na API obriga o usuário a baixar novamente ~680 MB.

**Recomendação:** adotar uma arquitetura em dois níveis — **núcleo** atualizado pelo `tauri-plugin-updater` (~72,90 MiB comprimido com motor de transcrição, ou ~16 MiB no perfil mínimo) + **recursos externos versionados** com manifesto assinado via `minisign` — começando pelo desacoplamento completo do pacote `first-video` (redução estimada de **~89%** no download de atualizações rotineiras) e evoluindo para pacotes granulares por componente (`tools`, `kokoro`, `factory-env`, `remotion-deps`).

---

## 1. Situação atual (evidências no código)

O empacotamento Windows atual acopla aplicativo desktop, backend Python e recursos pesados em um único artefato NSIS:

1. **Entrada única no Tauri (`frontend/src-tauri/tauri.conf.json`):**
   - `bundle.targets` gera `["nsis"]` com `installMode: "currentUser"` e `webviewInstallMode.type: "skip"`.
   - `bundle.resources` (linha 23) copia integralmente `../../backend/dist/horus-backend/` para `backend/` dentro do instalador. Tudo o que estiver nessa pasta entra no NSIS único.
2. **Três perfis de build (`scripts/build-backend.ps1` e overlays `scripts/tauri*.json`):**
   - **Padrão / mínimo** (`tauri.conf.json`): chama `build-backend.ps1` sem flags. Gera `horus-backend.exe` via PyInstaller `--onedir` excluindo explicitamente `torch`, `kokoro`, `yt_dlp`, `faster_whisper`, `ctranslate2`, `av` e `tokenizers`. Inclui as fontes de `backend/src/horus_backend` via `--add-data` para o bridge de execução externa.
   - **Transcrição** (`scripts/tauri-transcription.json` / `-IncludeTranscription`): usa `.venv-bundle-transcription` e o lock `scripts/backend-bundle-transcription-requirements.txt`, incluindo `faster_whisper`, `ctranslate2`, `av`, `tokenizers` e `onnxruntime` (Silero VAD). Não inclui pesos Whisper nem runtime CUDA completo (apenas o dispatcher `cudnn64_9.dll` do wheel CTranslate2).
   - **Primeiro vídeo / offline completo** (`scripts/tauri-first-video.json` / `-IncludeFirstVideo`): habilita `-IncludeTranscription` e executa `scripts/prepare-first-video.py`, populando `backend/dist/horus-backend/first-video/`.
3. **Montagem do `first-video` (`scripts/prepare-first-video.py` e `scripts/first-video-artifacts.json`):**
   - Extrai arquivos versionados da fábrica (`scripts/fabrica`, `remotion/src`, locks) a partir de `factory_revision`.
   - Baixa e extrai Python 3.12 relocável (`python-build-standalone`), instala `scripts/first-video-requirements.txt` (com `torch` CPU, `kokoro`, `spacy`, `transformers`, etc.) e remove `.lib`, `.pdb`, `__pycache__` e `torch/include`.
   - Baixa Node.js 22.23.5 x64 e executa `npm ci --ignore-scripts` em `factory/remotion`.
   - Copia `ffmpeg.exe`, `ffprobe.exe` (BtbN GPL 8.1) e pesos do Kokoro (`kokoro-v1_0.pth` e 3 vozes `.pt`).
   - Gera `runtime.json` com `schema_version: 1`, `profile: "first-video"`, `build_fingerprint` e o SHA-256 de cada arquivo.
4. **Resolução em runtime (`backend/src/horus_backend/bundled_runtime.py`):**
   - `runtime_root()` só ativa em executável congelado (`sys.frozen`) e lê exclusivamente `Path(sys.executable).resolve().parent / 'first-video' / 'runtime.json'`.
   - `resource(relative)` valida que o destino resolvido permanece dentro de `runtime_root()` (proteção contra junctions/symlinks).
   - `configured_resource(variable, relative)` em `config.py` dá prioridade a variáveis de ambiente (`HORUS_FFMPEG`, `HORUS_SUBTITLE_FONT`, `HORUS_KOKORO_PYTHON`, `HORUS_KOKORO_MODEL_DIR`, `FABRICA_HOME` / `HORUS_PILOT_FACTORY`) antes de recorrer ao pacote embutido.
   - `dependencies.py`, `jobs.py` e `production.py` usam `resource('tools')` e `tool_path()` para localizar `ffmpeg`, `ffprobe` e `node`.
5. **Fluxo atual de atualização (`frontend/src-tauri/src/updater.rs`):**
   - O `tauri-plugin-updater` (`=2.14.1`) baixa o instalador NSIS completo em memória (`Vec<u8>`), valida a assinatura `minisign` vinculada à versão, grava em `workspace/updater/installers/<versão>/setup.exe` e executa NSIS com `/UPDATE /P /R`. Não há suporte a delta ou download parcial.

---

## 2. Inventário de tamanho por componente

### 2.1 Medição real em disco (somente leitura)

Medição obtida por leitura direta de metadados em `%LOCALAPPDATA%\Programs\Horus` (instalação existente do perfil `first-video`):

| Camada / Caminho relativo | Arquivos | Tamanho (bytes) | Tamanho (MiB) | % do total | Origem / Observação |
|---|---:|---:|---:|---:|---|
| `horus-desktop.exe` | 1 | 15.957.504 | 15,22 | 0,7% | Binário Tauri 2 + WebView UI embutida |
| `uninstall.exe` | 1 | 403.365 | 0,38 | <0,1% | Desinstalador NSIS |
| `backend/horus-backend.exe` | 1 | 11.454.946 | 10,92 | 0,5% | Launcher PyInstaller do backend |
| `backend/_internal/` | 620 | 276.153.322 | 263,36 | 12,7% | Python 3.12 + FastAPI + motor de transcrição |
| **Subtotal Núcleo (com transcrição)** | **623** | **303.969.137** | **289,88** | **14,0%** | Tudo fora de `first-video/` |
| `backend/first-video/factory/scripts/.venv/` | 21.799 | 865.395.164 | 825,31 | 39,8% | Python 3.12 standalone + `site-packages` |
| `backend/first-video/factory/scripts/fabrica/` (+toml/lock) | 24 | 245.095 | 0,23 | <0,1% | Código Python da fábrica (`fabrica/` = 0,14 MiB) |
| `backend/first-video/factory/remotion/node_modules/` | 14.251 | 241.076.926 | 229,91 | 11,1% | Dependências npm do Remotion |
| `backend/first-video/factory/remotion/` (código + locks) | 9 | 150.103 | 0,14 | <0,1% | `src/` = 0,01 MiB; `package-lock.json` = 0,13 MiB |
| `backend/first-video/tools/` | 2.034 | 429.841.263 | 409,93 | 19,8% | `ffmpeg.exe` + `ffprobe.exe` + `node.exe` + npm |
| `backend/first-video/kokoro/` | 5 | 328.784.857 | 313,55 | 15,1% | Pesos TTS Kokoro-82M + 3 vozes |
| `backend/first-video/runtime.json` | 1 | 5.985.154 | 5,71 | 0,3% | Manifesto com hashes de 38.135 arquivos |
| `backend/first-video/licenses/` | 10 | 414.388 | 0,40 | <0,1% | Textos de licença e inventário npm |
| `backend/first-video/fonts/` | 2 | 190.916 | 0,18 | <0,1% | `ArchivoExpanded-SemiBold.ttf` + `OFL-Archivo.txt` |
| **Subtotal `first-video/`** | **38.135** | **1.872.083.866** | **1.785,36** | **86,0%** | Pacote de recursos offline |
| **Total instalado medido** | **38.758** | **2.176.053.003** | **2.075,25** | **100,0%** | **2,027 GiB** no disco |

#### Detalhamento dos maiores itens dentro de `backend/_internal/` (263,36 MiB medidos)

| Entrada | Arquivos | Tamanho (MiB) | Papel |
|---|---:|---:|---|
| `av.libs` + `av` | 264 | 82,02 | PyAV / FFmpeg libs para decodificação no Whisper |
| `ctranslate2` | 31 | 61,43 | Motor de inferência `faster-whisper` (inclui `cudnn64_9.dll` dispatcher) |
| `onnxruntime` | 3 | 44,07 | Runtime do Silero VAD (dependência do `faster-whisper`) |
| `numpy.libs` + `numpy` | 15 | 26,25 | Álgebra numérica |
| `hf_xet` + `tokenizers` | 28 | 16,77 | Tokenização e utilitários HuggingFace |
| `libcrypto-3-x64.dll` + `libssl-3-x64.dll` | 2 | 9,13 | TLS/criptografia Python |
| `python312.dll` + `pydantic_core` + `sqlite3.dll` + `ucrtbase.dll` | 4 | 15,04 | Runtime base Python/FastAPI/SQLite |
| Demais módulos (`uvicorn`, `fastapi`, `horus_backend`, etc.) | 273 | 8,65 | API, workers e stdlib |

#### Detalhamento dos maiores itens dentro de `first-video/` (1.785,36 MiB medidos)

- **`factory/scripts/.venv/Scripts/Lib/site-packages/` (792,59 MiB medidos):** `torch` CPU (335,66 MiB), `spacy` (88,69 MiB), `transformers` (54,53 MiB), `sympy` (37,41 MiB), `babel` (29,63 MiB), `blis` (21,60 MiB), `numpy` + `numpy.libs` (41,54 MiB), `espeakng_loader` (17,93 MiB), `misaki` (14,37 MiB), `PIL` (14,36 MiB), `pip` (10,95 MiB), `thinc` (10,50 MiB), `hf_xet` (9,07 MiB), `tokenizers` (7,76 MiB). O runtime Python 3.12 standalone (`DLLs`, `python312.dll`, `tcl`, `include`, `libs`) soma ~32,7 MiB.
- **`tools/` (409,93 MiB medidos):** `ffmpeg.exe` (157,78 MiB), `ffprobe.exe` (157,58 MiB), `node.exe` (83,04 MiB), `node_modules` do npm (11,28 MiB).
- **`kokoro/` (313,55 MiB medidos):** `kokoro-v1_0.pth` (312,05 MiB), `voices/pf_dora.pt` (0,50 MiB), `voices/pm_alex.pt` (0,50 MiB), `voices/pm_santa.pt` (0,50 MiB), `config.json` (2,3 KiB).

### 2.2 Valores de referência e estimativas

- **Instaladores já medidos em [empacotamento-windows.md](empacotamento-windows.md) e [adr-atualizador.md](adr-atualizador.md):**
  - Perfil mínimo (sem transcrição e sem `first-video`): **15,57 MiB a 16,84 MiB** comprimido (NSIS); `onedir` de 63 arquivos e **33,48 MiB** (35.110.262 bytes) descomprimido.
  - Perfil transcrição (sem `first-video`): **72,90 MiB** (76.445.050 bytes) comprimido (NSIS), correspondendo aos ~289,88 MiB descomprimidos medidos acima (~25% de taxa de compressão).
- **Instalador `first-video` completo:** **~680 MB** comprimido (referência do requisito no escopo HOR-100; não foi gerado novo build nesta tarefa).
- **Modelos Whisper:** **0 bytes** no instalador; são importados externamente para `workspace/models/` ou `HORUS_WHISPER_MODELS_DIR` (estimativa típica de ~460–500 MiB para o modelo `small`, não incluída na medição acima).

---

## 3. Frequência de mudança por componente

| Categoria | Componentes | Tamanho instalado | Frequência de mudança |
|---|---|---:|---|
| **Núcleo dinâmico** (muda sempre) | `horus-desktop.exe` (UI + Rust), `horus-backend.exe` + `horus_backend` (API, workflows, segurança, schema SQLite) | ~28–35 MiB | Toda release (correções, UI, contratos, migrações) |
| **Código leve da fábrica** | `factory/scripts/fabrica/` (0,14 MiB) + `factory/remotion/src/` (0,01 MiB) | ~0,15 MiB | Média/alta (ajustes de composição, legendas, cenas) |
| **Stack nativa de transcrição** | `backend/_internal/` (`av`, `ctranslate2`, `onnxruntime`, `faster_whisper`) | ~255 MiB | Baixa (apenas quando atualiza `faster-whisper`, PyAV ou Python) |
| **Ambientes de execução da fábrica** | `factory/scripts/.venv/` (`torch`, `spacy`, `transformers`, `kokoro`), `factory/remotion/node_modules/` | ~1.055 MiB | Baixa (somente em atualização de lockfile Python/npm) |
| **Ferramentas externas quase imutáveis** | `ffmpeg.exe`, `ffprobe.exe`, `node.exe` | ~398 MiB | Muito baixa (atualizações pontuais de versão maior/segurança) |
| **Pesos e ativos estáticos** | `kokoro/` (313,55 MiB), `fonts/` (0,18 MiB), `licenses/` | ~314 MiB | Quase nula (só se trocar modelo de voz padrão ou fontes) |
| **Modelos do usuário** | Modelos Whisper em `workspace/models/` | Externo | Gerenciado pelo operador, nunca empacotado no updater |

**Conclusão desta matriz:** 86% do peso instalado (`first-video/`) muda raramente, e mesmo dentro dele, o código que efetivamente evolui com frequência (`fabrica/` + `remotion/src/`) ocupa apenas **0,15 MiB** (menos de 0,01% do pacote).

---

## 4. Alternativas de arquitetura

### Alternativa A — Externalizar apenas pesos Kokoro e ferramentas (`kokoro/` + `tools/`)

- **O que muda:** move `kokoro/` (313,55 MiB) e `tools/` (`ffmpeg.exe`, `ffprobe.exe`, `node.exe` = 409,93 MiB) para um diretório externo de recursos. O instalador NSIS mantém `factory/scripts/.venv` (825,31 MiB) e `factory/remotion/node_modules` (229,91 MiB).
- **Economia estimada por atualização:** remove 723,48 MiB descomprimidos (**~35%** do total). Instalador cairia de ~680 MB para **~380–430 MB** comprimidos (estimativa).
- **Esforço:** baixo (**2–3 dias**).
- **Prós:** mínimo impacto em caminhos Python/Node (`pyvenv.cfg`, `horus_factory.pth`, `node_modules`).
- **Contras:** economia insuficiente — o usuário continuaria baixando ~400 MB a cada atualização de UI/API por causa de `torch` e `node_modules`. Não resolve o problema de fundo.

### Alternativa B — Separar Núcleo (perfil `transcription`) × Pacote `first-video` externo versionado

- **O que muda:**
  - O instalador NSIS do núcleo passa a ser o perfil `transcription` (**~72,90 MiB** comprimido / ~290 MiB instalado) — ou opcionalmente o perfil mínimo (~16–17 MiB) para instalações sem transcrição local.
  - Todo o diretório `first-video/` (1.785,36 MiB descomprimido / ~600 MB comprimido) sai de `backend/dist/horus-backend/` e passa a ser instalado/armazenado em diretório externo versionado de recursos (fora de `$INSTDIR`), resolvido por `bundled_runtime.py`.
  - O arquivo `runtime.json` já existente em `scripts/prepare-first-video.py` evolui para um manifesto assinado de recurso.
- **Economia estimada por atualização do núcleo:** de ~680 MB para **~73 MB** (**~89% de redução** no download).
- **Esforço:** médio (**5–7 dias** em incrementos).
- **Limitação:** se `fabrica/` (0,14 MiB) ou `remotion/src/` (0,01 MiB) mudarem e continuarem acoplados dentro de `first-video/`, uma alteração de 10 KB na fábrica exigiria novo pacote `first-video` (~600 MB). *Mitigação simples:* desacoplar o código leve (`fabrica/` + `remotion/src/`) para dentro do núcleo ou de um micro-pacote separado, mantendo `.venv` e `node_modules` imutáveis no pacote pesado.

### Alternativa C — Separação completa com manifesto assinado e pacotes granulares endereçados por conteúdo

- **O que muda:**
  - **Núcleo:** atualizado exclusivamente pelo `tauri-plugin-updater` (~73 MB comprimido com transcrição). Pode carregar `fabrica/` e `remotion/src/` (0,15 MiB) ou recebê-los como micro-pacote.
  - **Pacotes de recursos independentes**, cada um com `id`, `version`, `sha256`, assinatura `minisign`, plataforma/ABI, faixa de compatibilidade (`min_core_version` / `max_core_version`) e licença:
    1. `tools-win-x64` (`ffmpeg.exe`, `ffprobe.exe`, `node.exe` + npm — ~410 MiB instalado)
    2. `kokoro-v1` (pesos `kokoro-v1_0.pth` + vozes — ~314 MiB instalado)
    3. `factory-python-env` (Python 3.12 standalone + `site-packages` com `torch` — ~825 MiB instalado)
    4. `factory-remotion-deps` (`node_modules` do Remotion — ~230 MiB instalado)
    5. `factory-code` (`fabrica/` + `remotion/src/` + `fonts/` — ~0,33 MiB instalado, se não ficar no núcleo)
  - O aplicativo consulta o manifesto de recursos (`resources.json` + assinatura `minisign`) e baixa/verifica **apenas** os pacotes ausentes, inválidos ou incompatíveis.
- **Economia estimada por atualização:**
  - Release normal de UI/API: **~73 MB** (**~89% de economia**).
  - Atualização só de código da fábrica/legendas: **< 1 MB** adicional (em vez de ~600 MB).
  - Atualização só do FFmpeg ou só do Kokoro: baixa apenas o pacote correspondente.
- **Esforço:** médio/alto (**8–11 dias** em incrementos).

### Comparação de riscos, instalação, desinstalação e migração

| Aspecto | Alternativa A (`kokoro`+`tools`) | Alternativa B (`first-video` único externo) | Alternativa C (pacotes granulares assinados) |
|---|---|---|---|
| **Economia por update do núcleo** | ~35% (~380–430 MB) | **~89%** (~73 MB) | **~89%** (~73 MB) + updates parciais de recursos |
| **Compatibilidade ABI / Python** | Baixo risco (`.venv` segue no `backend/`) | Requer garantir compatibilidade entre `horus_backend` (núcleo) e `fabrica` / `kokoro_worker` no `.venv` externo | Igual à B, com `min_core_version`/`max_core_version` explícitos por pacote |
| **Caminhos e `MAX_PATH` Windows** | Sem mudança relevante | `node_modules` e `.venv` têm 36 mil arquivos com caminhos longos; extração e staging devem usar prefixo `\\?\` e raiz curta (ex.: `%LOCALAPPDATA%\Horus\res\`) | Mesmo cuidado com caminhos longos; diretório por `id/versão` reduz profundidade |
| **Antivírus / Windows Defender** | Baixo impacto adicional | Extrair ~38.000 arquivos (`.exe`/`.dll`/`.pyd`/`.js`) fora de `$INSTDIR` pode disparar varredura em tempo real e lentidão na primeira instalação | Igual à B na instalação inicial; em updates rotineiros **não toca** nos 38.000 arquivos |
| **Instalação inicial** | Um único NSIS (~400 MB), offline | Requer escolha: instalador "full offline" (núcleo + pacotes) **ou** instalador leve (~73 MB) + importação de pacote offline / download sob demanda | Mesmo que B; permite instalar núcleo leve e importar somente os pacotes necessários |
| **Instalação offline (sem internet)** | Nativa no NSIS | Requer suporte explícito a importar diretório/arquivo `.tar.zst` ou `.zip` assinado de pasta local/pendrive | Nativa via importador local de pacotes + manifesto assinado |
| **Licenças (FFmpeg GPL / Remotion)** | Separa FFmpeg GPL do binário proprietário/núcleo | Isola completamente FFmpeg GPL, Remotion e Kokoro (Apache-2.0) em pacote de recursos separado do núcleo | Melhor isolamento jurídico e de atribuição (`licenses/` por pacote) |
| **Desinstalação e preservação (HOR-147)** | Recursos externos não são apagados pelo `/UPDATE` | `uninstall.exe` do núcleo não deve apagar automaticamente recursos externos nem modelos/projetos do usuário | Mesmo que B; limpeza de versões antigas de recursos deve ser explícita e opcional |
| **Migração da versão `0.1.1` existente** | Requer mover ou rebaixar `kokoro`/`tools` antes do `/UPDATE` | **Crítico (ver abaixo):** `0.1.1` tem `first-video/` dentro de `$INSTDIR\backend\first-video`; antes que o novo NSIS sem `first-video` execute `/UPDATE`, é preciso preservar/migrar esses arquivos | Igual à B, podendo registrar o `first-video/` migrado como pacotes locais verificados via `runtime.json` |

#### Detalhe crítico: como migrar uma instalação `0.1.1` existente sem perder nada

Na instalação `0.1.1` medida em `%LOCALAPPDATA%\Programs\Horus\backend\first-video`, todos os 38.135 arquivos estão **dentro da pasta de instalação do aplicativo (`$INSTDIR`)**, acompanhados do `runtime.json` com seus hashes SHA-256. Se uma atualização `0.1.2` enviar um NSIS sem `first-video`, existem dois riscos:
1. O instalador NSIS pode apagar ou sobrescrever subpastas de `$INSTDIR\backend`, destruindo o runtime funcional que o usuário já tinha e forçando novo download de ~600 MB.
2. Alternativamente, se deixar arquivos órfãos em `$INSTDIR\backend\first-video`, o `bundled_runtime.py` antigo continuaria lendo a cópia presa na pasta do app.

**Estratégia segura de migração (`0.1.1` → `0.1.2+`):**
- **Passo 1 (pré-`/UPDATE`, ainda no updater do `0.1.2` ou em hook antes de rodar o NSIS):** verificar se existe `$INSTDIR\backend\first-video\runtime.json`. Caso exista e o diretório externo de recursos ainda não possua aquela revisão válida, **validar os hashes** contra `runtime.json` e **mover/copiar atomicamente** para o diretório externo de recursos (ex.: `%LOCALAPPDATA%\Horus\resources\first-video\<fingerprint>\`) antes de lançar o NSIS.
- **Passo 2 (fallback temporário):** enquanto houver instalações em transição, `bundled_runtime.py` consulta primeiro o diretório externo ativo (selecionado por compatibilidade) e mantém fallback de leitura para `$INSTDIR\backend\first-video` se válido.
- **Passo 3 (rollback manual):** se o usuário precisar voltar manualmente para `0.1.1`, o diretório externo permanece intacto e a `0.1.1` instalada restaura seu próprio `first-video` (ou lê via variável de ambiente), sem perder projetos, bancos SQLite ou modelos Whisper em `workspace/models/`.

---

## 5. Regras de compatibilidade, integridade e segurança

Para qualquer alternativa adotada (B ou C), o contrato de recursos deve seguir estas regras obrigatórias:

1. **Identificação canônica e independência de versão:**
   - Todo pacote é identificado por `(id, version, sha256, platform_abi)`.
   - **Nunca** usar a versão SemVer do aplicativo (`0.1.0`, `0.1.2`) como versão de modelo ou recurso. Um recurso `kokoro-v1` versão `1.0.0` continua válido entre dezenas de versões do núcleo.
   - O manifesto declara `min_core_version` e `max_core_version` (ou `abi_tag`, ex.: `horus-factory-abi-1`) para impedir que um núcleo novo execute um worker Python incompatível.
2. **Verificação criptográfica sem criptografia inventada:**
   - O `frontend/src-tauri/Cargo.lock` já contém **`minisign-verify`**, **`sha2`**, **`zip`**, **`tar`** e **`flate2`** (trazidos pelo `tauri-plugin-updater` e `tauri`).
   - O manifesto de recursos (`resources.json`) deve ser assinado com **minisign** (mesma família de chave/verificação já auditada no HOR-100, sem criar algoritmo ou formato próprio) e cada arquivo/pacote validado por **SHA-256** antes da extração e após a extração.
3. **Idempotência (instalar só se ausente ou inválido):**
   - Antes de qualquer download ou cópia, verificar se o `(id, version, sha256)` já está presente e íntegro no repositório local de recursos. Se válido, **zero requisições de rede e zero reescritas em disco**.
4. **Staging isolado e ativação atômica:**
   - Download e descompactação ocorrem sempre em pasta de staging (ex.: `resources/.staging/<uuid>/`).
   - Somente após conferir assinatura do manifesto, SHA-256 do arquivo comprimido e SHA-256 dos arquivos extraídos (e checagem de traversal para impedir `..` ou junctions externas), o diretório é movido/renomeado para o destino final e o ponteiro de versão ativa (`active.json` escrito de forma atômica via arquivo temporário + replace) é atualizado.
   - Falha ou queda de energia no meio do processo nunca corrompe o recurso atualmente ativo.
5. **Preservação de versões mais novas e recursos personalizados:**
   - Nunca sobrescrever um recurso mais novo ou personalizado por uma versão antiga durante atualização ou reparo.
   - Variáveis de ambiente explícitas (`HORUS_FFMPEG`, `HORUS_KOKORO_MODEL_DIR`, `HORUS_WHISPER_MODELS_DIR`, `FABRICA_HOME`, etc.) mantêm precedência absoluta sobre o diretório gerenciado.
   - Versões diferentes de um mesmo pacote ficam **lado a lado** (endereçadas por versão/hash). O núcleo seleciona a versão compatível; se nenhuma for compatível e não houver canal disponível, bloqueia a funcionalidade afetada com mensagem clara (sem destruir o recurso existente).
6. **Operação offline e proteção de modelos do usuário:**
   - O aplicativo deve aceitar importação offline de pacotes de recursos (de pasta local ou arquivo assinado escolhido pelo usuário), validando assinatura e SHA-256 da mesma forma que o canal remoto.
   - Nunca disparar downloads implícitos durante inicialização ou durante execução de jobs.
   - **Proibição de remoção automática:** modelos importados pelo usuário (`workspace/models/`) e pacotes de recursos antigos não devem ser apagados automaticamente pelo atualizador. Qualquer limpeza de versões antigas deve ser ação explícita do operador.

---

## 6. Relação com o canal de atualização e com o updater nativo do Tauri

1. **Papel do `tauri-plugin-updater`:**
   - Permanece responsável **exclusivamente** pelo **núcleo** (app desktop + backend PyInstaller).
   - Continua usando `latest.json` + NSIS assinado (`.sig`) + fluxo já validado em `updater.rs` (confirmação humana, reserva de jobs em `/v1/update/reservation`, backup SQLite verificável, shutdown limpo, `/UPDATE /P /R` e recibo de saúde pós-reinício).
   - **Ganho direto:** o `Vec<u8>` baixado em memória e o cache em `workspace/updater/installers/<versão>/` caem de ~680 MB para ~73 MB (perfil transcrição), reduzindo consumo de RAM, disco e risco de timeout no proxy.
2. **Canal de recursos alinhado à decisão de 16/11/2026:**
   - **Fase atual (validação / pasta local ou de rede):** o usuário indica uma pasta local/SMB contendo o pacote de núcleo (`latest.json` + NSIS) e, quando aplicável, o repositório de recursos (`resources.json` + `.minisig` + pacotes). A importação/verificação lê da pasta, valida `minisign` + `sha256` e ativa atomicamente, sem hospedagem externa.
   - **Fase futura (proxy HTTPS privado com autenticação revogável):** o mesmo proxy privado que servirá `latest.json` e o NSIS do núcleo passa a servir `resources.json`, `resources.json.minisig` e os pacotes imutáveis de recursos sob a mesma política TLS, cabeçalho de autorização por instalação e streaming sem redirecionamentos abertos.
   - **Regra de rede e privacidade (`F-21`):** qualquer consulta remota futura de `resources.json` deve passar pelo mesmo cliente Rust controlado e ser registrada na revisão da entrada `F-21` em [inventario-fluxos-dados.md](inventario-fluxos-dados.md) e na trava `test_network_inventory.py`, mantendo zero requisições no build padrão sem canal configurado.

---

## 7. Recomendação, plano em incrementos e decisões pendentes

### 7.1 Recomendação fundamentada

Recomenda-se aprovar a **Alternativa B evoluindo de forma compatível para a Alternativa C**:

1. **No curto prazo (maior impacto por menor risco):** separar o **núcleo** (perfil `transcription`, ~72,90 MiB comprimido) do **pacote `first-video` externo** (~1,74 GiB descomprimido), aproveitando a estrutura já existente de `prepare-first-video.py` e `bundled_runtime.py`. Além disso, mover `fabrica/` + `remotion/src/` (0,15 MiB) para dentro do núcleo (ou mantê-los como micro-pacote versionado separado do `.venv`/`node_modules`), evitando que ajustes pequenos na fábrica forcem download de ~600 MB.
2. **Já estruturar o manifesto assinado no formato da Alternativa C (`resources.json` + `minisign`):** mesmo que inicialmente haja apenas 1 ou 2 pacotes (`first-video-runtime` e `factory-code`), usar o formato granular desde o início permite fatiar depois (`tools`, `kokoro`, `factory-python-env`, `factory-remotion-deps`) sem quebrar o cliente.

### 7.2 Plano sugerido em incrementos pequenos (após aprovação)

| Incremento | Escopo e arquivos principais | Critério de aceite / testes |
|---|---|---|
| **A6.1 — Contrato e manifesto assinado de recursos** | Schema `resources.json`, compatibilidade `min/max_core_version`, verificação `minisign-verify` + `sha2` em Rust (reutilizando crates do `Cargo.lock`). | Testes unitários Rust: assinatura válida/adulterada, hash divergente, recusa de downgrade/incompatibilidade, path traversal bloqueado. |
| **A6.2 — Diretório externo e resolução no backend** | Atualizar `bundled_runtime.py` para resolver diretório externo versionado (`%LOCALAPPDATA%\Horus\resources` ou `HORUS_RESOURCES_DIR`) + fallback temporário para `$INSTDIR\backend\first-video`. | Testes Python: precedência de variáveis de ambiente, proteção contra junction externa, seleção de versão compatível, fallback funcional. |
| **A6.3 — Staging, ativação atômica e importação offline** | Fluxo Rust de importação de pasta/arquivo local, staging com prefixo `\\?\`, verificação de todos os hashes e ativação atômica (`active.json`). | Testes com arquivo truncado, interrupção no staging (recurso ativo intacto), recurso já válido (zero cópia/GET), recurso mais novo preservado. |
| **A6.4 — Migração segura da `0.1.1` e desacoplamento do NSIS** | Migração pré-`/UPDATE` de `$INSTDIR\backend\first-video` para o diretório externo (usando `runtime.json`); build do núcleo sem `first-video`. | Ensaio E2E em VM/sandbox: atualizar `0.1.1` (com `first-video`) → `0.1.2` (núcleo enxuto) com **zero download de recursos**, diagnóstico `production.ready=true` e dados intactos. |
| **A6.5 — Granularidade e canal HTTPS (quando canal estiver pronto)** | Divisão do pacote em blocos (`tools`, `kokoro`, `factory-env`, `remotion-deps`) e suporte ao canal pasta local/proxy HTTPS. | Atualização de núcleo demonstra **0 GET** de pacotes inalterados; atualização de `fabrica` baixa < 1 MB. |

### 7.3 Decisões que o usuário precisa tomar antes da implementação

1. **Aprovar a estratégia arquitetural:** confirmar **Alternativa B evoluindo para C** (núcleo com transcrição ~73 MB + recursos externos assinados) ou preferir outra alternativa (A ou B pura).
2. **Experiência da instalação inicial (primeiro instalador):**
   - *(Opção 1 — Recomendada para offline)* Disponibilizar um "instalador/pacote completo de primeira instalação" (ou pasta de distribuição offline com núcleo + pacotes) para novos computadores sem internet, enquanto as **atualizações** baixam apenas o núcleo (~73 MB).
   - *(Opção 2)* Instalador inicial sempre leve (~73 MB) que pede ao usuário para importar o pacote de recursos ou baixá-lo na primeira abertura.
3. **Desacoplamento do código leve da fábrica (`fabrica/` + `remotion/src/`, 0,15 MiB):** autorizar incluir esses 0,15 MiB no núcleo (ou em micro-pacote próprio) para que atualizações da fábrica não exijam rebaixar `.venv` + `node_modules` (~1,05 GiB instalados).
4. **Localização padrão do repositório externo de recursos no Windows:** confirmar `%LOCALAPPDATA%\Horus\resources\` (compartilhável e independente de `$INSTDIR`, alinhado a `HORUS_DATA_DIR` / `update-cache`) ou `%LOCALAPPDATA%\com.horus.audiovisual\resources\`.
5. **Política de retenção de versões antigas de recursos:** manter a regra padrão de **nunca remover automaticamente** (limpeza apenas manual pelo usuário em Ajustes/disco) ou definir limite automático (ex.: manter apenas N versões anteriores após validação de saúde).
6. **Chave de assinatura dos recursos:** usar a mesma chave pública `minisign` de produção do atualizador do núcleo (simplifica custódia) ou chave dedicada de recursos.
