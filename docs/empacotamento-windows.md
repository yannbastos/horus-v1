# Empacotamento Windows

O desktop release inicia `backend/horus-backend.exe` relativo aos recursos
instalados. O backend usa PyInstaller **onedir** (Python e bibliotecas locais
incluídos), porta dinâmica, token por sessão e stdin supervisionado pelo Tauri.
O Job Object do Windows encerra descendentes se o desktop morrer; ao fechar
normalmente, o desktop solicita `/v1/shutdown` e aguarda até cinco segundos.
O prazo de inicialização é 45 segundos para acomodar cache frio/antivírus.

## Gerar localmente

Pré-requisitos de **build**: Windows x64, Python 3.12 via uv, Node/npm,
Rust/MSVC e ferramentas Tauri/NSIS. Nenhum deles é exigido pelo backend instalado.
WebView2 deve estar previamente instalado no Windows; o instalador não baixa
runtime nem modelos. Sem assinatura, publicação ou atualização automática.
Nota: decisões parciais e pendências sobre o futuro atualizador constam em [adr-atualizador.md](adr-atualizador.md) (implementação não iniciada).

```powershell
npm ci --prefix frontend
npm --prefix frontend run tauri build
```

O hook Tauri chama `scripts/build-backend.ps1`: prepara exclusivamente
`backend/.venv-bundle`, sincroniza versões/hashes de
`scripts/backend-bundle-requirements.txt`, instala o projeto local sem resolver
novamente dependências e gera `backend/dist/horus-backend/`. Não usa a `.venv`
de desenvolvimento nem seus extras. Para gerar somente o backend:

```powershell
powershell -NoProfile -File scripts/build-backend.ps1
```

O NSIS fica em `frontend/src-tauri/target/release/bundle/nsis/`; instalação
`currentUser`, sem elevação. `-SkipInstall` reaproveita o ambiente de build já
sincronizado (não é o padrão). Para atualizar o lock deliberadamente:

```powershell
uv pip compile backend/pyproject.toml --extra bundle --python-version 3.12 --python-platform windows --generate-hashes --output-file scripts/backend-bundle-requirements.txt
```

## Limites do pacote

### Perfil opcional de transcrição e runtime CUDA explícito

O pacote mínimo continua sendo o padrão. Para incluir faster-whisper,
CTranslate2, PyAV e tokenizers (sem pesos Whisper ou runtime CUDA completo), gere explicitamente:

```powershell
npm --prefix frontend run tauri build -- --config ../scripts/tauri-transcription.json
# Somente backend desse perfil:
powershell -NoProfile -File scripts/build-backend.ps1 -IncludeTranscription
```

Esse perfil usa `backend/.venv-bundle-transcription` e o lock separado com hashes
`scripts/backend-bundle-transcription-requirements.txt`; o ambiente mínimo não
recebe os extras. O wheel CTranslate2 fornece um pequeno dispatcher `cudnn64_9.dll`,
mantido para importar o motor inclusive em CPU; cuBLAS e os componentes cuDNN
externos não são incluídos. O asset Silero VAD do faster-whisper também acompanha
o motor; não é um modelo Whisper. Para atualizar deliberadamente o lock:

```powershell
uv pip compile backend/pyproject.toml --extra bundle --extra transcription --python-version 3.12 --python-platform windows --generate-hashes --output-file scripts/backend-bundle-transcription-requirements.txt
```

O pacote também inclui as fontes Python de `horus_backend` como recursos do bridge,
incluindo `production_worker.py`, seus auxiliares e `narration/kokoro_worker.py`:
o Python externo configurado em `HORUS_KOKORO_PYTHON` não consegue executar um
módulo armazenado somente no PYZ do PyInstaller. Pesos e ambiente Kokoro continuam
externos; essa inclusão não instala Kokoro, não baixa modelos e não habilita
geração paga. O modelo small importado por `/v1/transcription/models/import`
fica disponível ao diagnóstico e alinhamento da produção sem reiniciar o app;
`HORUS_ALIGNMENT_MODEL` continua tendo precedência quando configurado.

### Ensaio do primeiro vídeo pela API do desktop instalado

```powershell
& backend/.venv/Scripts/python.exe scripts/test-installed-first-video.py `
  --installer '<NSIS com transcrição>' --factory '<fábrica local preparada>' `
  --whisper-source '<small local completo>' --kokoro-python '<python Kokoro>' `
  --kokoro-model-dir '<pesos Kokoro existentes>' --ffmpeg '<ffmpeg.exe local>' `
  --report-dir '<pasta de evidências do ensaio>'
```

O ensaio recusa instalação preexistente, instala por usuário em pasta temporária,
extrai o token apenas em memória do backend filho do desktop, consulta diagnóstico,
importa small sem rede, testa Kokoro e percorre proposta/roteiro fictícios,
produção draft `local_pilot`, QC, revisão por hash e exportação. Aprovações e
licenças são exclusivamente fixtures de teste, sem autorização de publicação.
Ao falhar, registra a etapa/erro e preserva os dados temporários para investigação;
fecha a árvore e desinstala, aguardando também a remoção assíncrona da entrada HKCU.
O roteiro exige `production_worker.available` e `production.ready` após importar
o modelo. Nenhum passo usa o banco operacional ou altera configuração global.

### Workers no executável instalado

O backend despacha uma lista fixa de comandos internos: `production-worker`,
`narration-worker`, `sapi-worker`, `pilot-windows-worker` e `alignment-worker`.
No pacote, cada subprocesso usa o próprio executável com esse comando, sem
`python -m` nem execução de um `.py` pelo executável congelado. Em desenvolvimento,
os comandos anteriores são preservados. A API só é importada no processo servidor.

O render continua no Python independente da fábrica, executando o bridge `.py`
empacotado e seus auxiliares; Node, Remotion, navegador, FFmpeg e modelos permanecem
externos, previamente preparados, sem downloads no job. Os processos continuam
sob posse e cancelamento existentes e recebem o ambiente mínimo da produção.

`production.ready` também exige que o executável responda ao `production-check`
com o protocolo esperado: o processo importa os workers e valida os recursos do
bridge. Erro, resposta inválida ou timeout tornam o check `production_worker`
indisponível e impedem iniciar produção. Isso comprova despacho/importação, sem
executar mídia ou carregar modelos no diagnóstico; não certifica qualidade de
voz, licenciamento ou sucesso de todo render em qualquer configuração externa.

**Baseline anterior ao suporte dos workers congelados (14/11/2026):** importação local, reconhecimento
de small para alinhamento, preferência e prévia Kokoro funcionaram no pacote
corrigido. A produção `local_pilot` foi recusada em `prepare` com “Worker de
produção requer backend Python preparado; empacotamento ainda não homologado.”
Logo, revisão/QC/exportação do primeiro vídeo **não foram alcançados no instalado**.
Naquele baseline, a guarda foi preservada e o diagnóstico podia retornar
`production.ready=true` sem verificar essa capacidade; o comportamento acima
substitui essa limitação.
O ensaio confirmou desinstalação e ausência de processos órfãos.

**Validação do suporte congelado em 14/11/2026:** o harness passou no NSIS com
transcrição, usando apenas recursos locais preparados e dados temporários:
bridge ausente tornou o check indisponível; restaurado o recurso e importado
small, `production.ready=true`; Kokoro → ASR → Remotion/fábrica → QC → revisão
fictícia por hash → ZIP concluíram no backend filho do Tauri instalado.
MP4 de 540×960, 30 fps e 21,013 s, `speech_aligned=true`; QC com 16 checks
aprovados, um aviso de proveniência/licença e nenhum erro bloqueante.
Exportação permaneceu `publishable=false`. Desinstalação concluída, nenhum
órfão e diretório temporário removido. Isso não substitui revisão audiovisual
humana nem valida atualização/rollback ou instalação em máquina limpa.

No Windows, a transcrição usa CPU int8 por padrão quando não há runtime
explicitamente selecionado. Para usar um runtime existente, inicie o desktop
com a configuração local (nenhuma alteração de PATH global ou instalação):

```powershell
$env:HORUS_CUDA_RUNTIME_DIR = '<pasta local absoluta confiável>'
$env:HORUS_WHISPER_MODELS_DIR = '<raiz dos modelos locais já preparados>'
$env:HORUS_WHISPER_DEVICE = 'cuda' # auto também permite GPU após seleção do runtime
& '<pasta instalada>/horus-desktop.exe'
```

Alternativamente, `HORUS_CUDA_RUNTIME_DIR=system` autoriza descoberta explícita
por `CUDA_PATH`, `CUDA_PATH_V<major>_<minor>` e cuDNN em Program Files/NVIDIA/CUDNN/v9*.
Não há busca arbitrária no PATH. A pasta escolhida pode conter as DLLs
diretamente, em `bin/`, `bin/x64/`, ou no layout `nvidia/{cublas,cudnn,cuda_nvrtc}/bin`
(nesse caso selecione a raiz `nvidia`). Caminhos relativos, UNC/dispositivos,
pastas inexistentes e escapes recusados por `file_access.client_path` não são usados.

`GET /v1/diagnostics` acrescenta o check e objeto `cuda_runtime`, preservando os
campos anteriores. A inspeção só lê arquivos: PE/DLL x64, nomes ABI cuBLAS 12 e
componentes cuDNN 9, FileVersion cuDNN quando presente e CTranslate2 >=4.5,<5
quando instalado; não importa motores nem carrega DLLs NVIDIA. FileVersion
cuBLAS pode usar numeração de driver 6.14 e não é confundido com sua ABI 12;
alguns componentes cuDNN não possuem recurso de versão. Presença estática não
certifica driver, GPU, autenticidade ou compatibilidade completa; use somente
DLLs NVIDIA confiáveis já provisionadas pelo operador.

Somente no subprocesso de transcrição, antes de importar os motores, o backend
registra as pastas com `os.add_dll_directory`, pré-carrega por caminho absoluto
e mantém referências/handles até terminar; consulta `cublasGetProperty` e
`cudnnGetVersion` para conferir as versões nativas. A configuração passa pela
allowlist exclusiva de `transcription.run`. Runtime ausente/inválido, carga
recusada ou GPU indisponível usam CPU int8; falha CUDA antes do primeiro trecho
permite uma tentativa CPU, e texto parcial nunca é repetido automaticamente.
Não há download de runtime/modelos durante a execução. O pacote mínimo informa
motor ausente: runtime sozinho não habilita transcrição nem fallback executável.

Ensaio instalado com áudio **sintético**, modelos já preparados e dados temporários:

```powershell
powershell -NoProfile -File scripts/test-windows-installer.ps1 `
  -Installer '<NSIS do perfil de transcrição>' -WithTranscription `
  -CudaRuntimeDir '<runtime local existente>' -ModelDir '<raiz dos modelos locais>' `
  -Audio '<WAV sintético descartável>'
```

O teste instala em pasta exclusiva, verifica diagnóstico, executa transcrições
solicitando CUDA sem runtime, com pasta inexistente e com UNC (espera CPU), e
com pasta válida e descoberta `system` (espera CUDA), conferindo o dispositivo efetivo nos eventos
persistidos; fecha o desktop, exige ausência de órfãos e desinstala ao fim.

Evidência local de 14/11/2026, base `8c7eb95`, RTX 3050 Laptop 6 GB/driver
621.24: NSIS desse perfil com **76.445.050 bytes (72,90 MiB)**, sem pesos Whisper
ou runtime NVIDIA completo. O ensaio acima passou com sete smokes instalados e
cinco jobs reais sobre WAV sintético e modelo small local: CPU int8 com runtime
ausente (9,394 s), pasta inexistente (4,396 s) e UNC (4,189 s); CUDA float16 com
pasta escolhida (2,610 s) e descoberta `system` (2,555 s). O dispositivo foi
conferido nos eventos persistidos. PATH do desktop limitado a System32,
`HORUS_DATA_DIR` temporário, sem download de modelos/DLLs ou mudança global.
Seis fechamentos normais e uma queda sem backend/descendentes órfãos;
desinstalação em 4,455 s com executável e registro HKCU removidos.
É evidência nessa máquina, não homologação de todos os drivers/GPUs ou VM limpa.

O pacote mínimo não inclui faster-whisper/CTranslate2/PyAV, Kokoro, yt-dlp, FFmpeg, Node,
fábrica externa ou modelos. A API e a interface iniciam sem essas dependências;
`/v1/diagnostics` informa o que falta. Produção/transcrição dependem da preparação
externa correspondente: este pacote não certifica o primeiro vídeo. O pacote
não executa downloads na inicialização e não configura Hermes real.

## Ensaio isolado

Nunca usar dados reais para validar instaladores. O smoke cria seu próprio
`HORUS_DATA_DIR` temporário, limpa configurações herdadas e limita PATH ao
System32. Requer `psutil` no Python **do teste**, fora da distribuição.

```powershell
uv venv --python 3.12 backend/.venv
uv pip install --python backend/.venv/Scripts/python.exe './backend[test,measure]'
& backend/.venv/Scripts/python.exe scripts/smoke-windows-package.py backend/dist/horus-backend/horus-backend.exe
# Depois de instalar por usuário em uma pasta temporária:
& backend/.venv/Scripts/python.exe scripts/smoke-windows-package.py '<pasta temporária>/horus-desktop.exe' --desktop
& backend/.venv/Scripts/python.exe scripts/smoke-windows-package.py '<pasta temporária>/horus-desktop.exe' --desktop --crash
# Ou executar instalação, ambos os smokes e desinstalação juntos:
powershell -NoProfile -File scripts/test-windows-installer.ps1 -Installer '<instalador.exe>'
```

Verifica health autenticado, diagnóstico dos extras ausentes, criação do banco
temporário e ausência de backend órfão após saída normal e queda do desktop.
O token é obtido somente em memória pelo processo de teste e não é impresso.
Instalação silenciosa: `instalador.exe /S /D=<pasta absoluta temporária>`;
desinstalação: executar o `uninstall.exe` dessa mesma pasta com `/S`.
Não executar desinstaladores de outra instalação preexistente.
O script completo recusa execução se houver HORUS previamente registrado no
HKCU e usa uma pasta temporária exclusiva para a instalação.

O ensaio no computador de desenvolvimento com PATH mínimo não equivale a uma
VM limpa, nem valida atualização, migração ou rollback.

## Atualização e rollback manual isolados

Gere os instaladores N e N+1 sequencialmente (memória limitada), sem alterar
manifestos versionados; N+1 muda somente o número no override Tauri local:

```powershell
powershell -NoProfile -File scripts/build-upgrade-installers.ps1
powershell -NoProfile -File scripts/test-windows-installer.ps1 `
  -Installer installer-test-artifacts/HORUS_0.1.0_x64-setup.exe `
  -UpgradeInstaller installer-test-artifacts/HORUS_0.1.1_x64-setup.exe
```

O segundo parâmetro habilita N → N+1 → N na mesma pasta instalada, com o mesmo
`HORUS_DATA_DIR` temporário exclusivo. Após iniciar N, o teste grava uma linha
sintética em `media`, um projeto JSON, um vídeo MPEG-4 sintético e uma preferência;
verifica SHA-256 dos quatro arquivos e `PRAGMA integrity_check`
antes e depois de iniciar cada reinstalação. Os smokes autenticados verificam
health, diagnostics e ausência de backend órfão após fechamento normal e queda.
A desinstalação ocorre em `finally`, inclusive após falha do ensaio, e a preservação
dos dados semeados também é conferida após desinstalar. O perfil WebView fica isolado
com `WEBVIEW2_USER_DATA_FOLDER` no diretório do smoke. A limpeza remove apenas a chave
de local de instalação criada pelo próprio ensaio e exige que o atalho tenha saído.

O template NSIS atual pode apagar dados quando o usuário marca a caixa de exclusão
na desinstalação interativa; `/UPDATE` bloqueia essa exclusão e o ensaio usa somente
`/S`, sem marcar a caixa. Detalhes, inventário e limites em
[ADR do atualizador — etapa 1](adr-atualizador.md#protótipo-local--etapa-1).

Como as versões diferem só pelo número, ambas usam o mesmo schema: esse ciclo
valida rollback manual compatível, sem migração real entre versões de schema.
Um teste negativo separado copia o banco temporário, aumenta `user_version`
em um e executa o backend instalado: exige saída com erro, nenhuma indicação
`ready`, mensagem `Banco criado por uma versão mais recente do Hórus. Migração
recusada.` e hash SHA-256 do banco inalterado. A mensagem é verificada no stderr
do backend; isso não comprova apresentação desse erro na janela do desktop.
Não há downgrade automático de schema: para uma versão antiga incompatível,
é necessário recuperar backup compatível em procedimento separado e validado.
Não há auto-update, assinatura, publicação nem acesso aos dados reais.

## Evidência local — 14/11/2026

Windows x64, Python 3.12.16, PyInstaller 6.22.3, Tauri 2.12.5.
`npm --prefix frontend run tauri build` gerou um NSIS de **16.324.585 bytes
(15,57 MiB)**; compilação Rust release em 7 min 13 s e congelamento Python
em 105,34 s. Diretório onedir: 63 arquivos, 35.110.262 bytes.

`scripts/test-windows-installer.ps1` passou com instalação silenciosa por usuário
em 5,288 s e desinstalação em 2,368 s, verificando a remoção do executável e
da entrada HKCU. Health e diagnostics autenticados responderam em ambos os
smokes instalados: inicialização normal 4,262 s (ciclo 9,861 s), inicialização
seguida de queda forçada 1,000 s (ciclo 1,814 s), sem backend órfão em ambos.
O binário sozinho iniciou em 0,767 s com cache aquecido; uma partida fria
anterior levou 18,118 s. Tempos variam com cache e antivírus.

API, snapshot OpenAPI e diagnóstico: **58 testes aprovados**, 123,38 s;
um aviso de depreciação Starlette/httpx. O contrato da API permaneceu intacto.
Não equivale à validação em máquina limpa nem ao aceite de atualização/rollback.

### Ensaio adicional de atualização e rollback — 14/11/2026

Na base `ac1759b`, os builds sequenciais geraram NSIS 0.1.0 de 16.347.013 bytes
e 0.1.1 de 16.347.135 bytes; só o número Tauri foi alterado por override local.
`test-windows-installer.ps1 -Installer …0.1.0… -UpgradeInstaller …0.1.1…`
terminou com código zero: registro HKCU confirmou 0.1.0 → 0.1.1 → 0.1.0,
linha sintética SQLite e arquivo preservados, integridade `ok`, sete smokes
autenticados aprovados (quatro fechamentos normais e três quedas), sem backend
ou descendentes órfãos. Inicialização após upgrade: 4,563 s; após rollback:
2,001 s. Instalação inicial: 4,809 s; desinstalação: 1,295 s, com executável e
entrada HKCU removidos ao fim.

Ambos os backends instalados recusaram cópia com schema v10 (suportam v9),
com mensagem explícita no stderr e SHA-256 do banco inalterado. Isso valida
o guard de incompatibilidade, sem certificar uma migração real ou a mensagem
na interface. Ensaio no computador de desenvolvimento, não em máquina limpa;
os critérios mais amplos de instalação/migração em VM continuam pendentes.

## Diretório inicial e isolamento de ensaios (HOR-147)

O template `frontend/src-tauri/installer.nsi` é a cópia do template oficial
`tauri-cli-v2.12.4` (`crates/tauri-bundler/src/bundle/windows/nsis/installer.nsi`),
com alterações limitadas ao destino inicial e `RestorePreviousInstallLocation`.
Ao atualizar a CLI, revisar o diff com upstream e repetir os testes abaixo.
Não substituímos o fluxo Tauri de atualização/desinstalação.

Instalação nova: `%LOCALAPPDATA%\Programs\Horus`, `currentUser`, sem elevação.
Locais personalizados permanentes continuam sendo respeitados. O template original
lia `Software\horus\HORUS` e restaurava seu valor padrão sem validar TEMP;
`/D=` grava esse valor na instalação e a desinstalação `/S` mantém o local lembrado.
O harness de primeiro vídeo não limpava essa chave. Isso explica a herança de
pasta de ensaio; o valor histórico exato já foi substituído pela instalação real,
portanto não pode ser recuperado do registro atual.

Uma lembrança sob TEMP sem entrada de desinstalação é ignorada somente na seleção
inicial, sem escrever registro. Se houver uma instalação registrada sob TEMP, o
instalador interrompe com código 2: não muda o destino silenciosamente nem deixa
os executáveis antigos órfãos. `/D=` continua sendo uma escolha explícita.

**Migração proposta, ainda dependente de aprovação do usuário:** inventariar
local/versão atuais; fechar o app por ação do operador; guardar instalador anterior,
backup verificado dos dados e inventário de mídias/segredos/perfil WebView;
desinstalar explicitamente a versão antiga sem selecionar exclusão de dados;
confirmar remoção dos executáveis, entrada Uninstall e atalhos; remover somente a
lembrança de instalação comprovadamente antiga, com autorização; instalar no destino
aprovado e conferir saúde, dados e atalhos antes de limpar qualquer sobra.
Instalações permanentes personalizadas permanecem no local atual até essa decisão.
Nenhuma migração ou limpeza da instalação operacional foi aplicada nesta entrega.

Todos os artefatos usados pelos harnesses devem ser construídos com identidade
`HORUS Test` / `com.horus.installer-test`; só mudar o identificador não isola as
chaves NSIS, que dependem do nome do produto. Para transcrição/primeiro vídeo:

```powershell
npm --prefix frontend run tauri build -- --config ../scripts/tauri-first-video.json --config ../scripts/tauri-installer-test.json
```

`build-upgrade-installers.ps1` e `build-updater-test.ps1` já aplicam a identidade.
Os harnesses verificam `ProductName` do instalador antes de executá-lo e recusam
artefatos antigos HORUS; reconstruí-los, nunca reutilizar o instalador privado de
validação como fixture. Os testes usam `/D=` sob TEMP e `HORUS_DATA_DIR` externo a
`app/`. O smoke de executável não instala produto nem escreve registro NSIS.
O ciclo de updater deve usar o mesmo overlay E2E em N e N+1 para isolar também o
WebView após relançamento. Não executar ensaios concorrentes com a mesma identidade.
O modo diagnóstico `--keep-installed` é excepcional: exige limpeza posterior do
produto de teste e não é um ensaio concluído com limpeza.

Regressões sem dependências Python extras:

```powershell
python scripts/test-installer-location.py
powershell -NoProfile -File scripts/test-installer-data-isolation.ps1 -Installer '<HORUS Test N>' -UpgradeInstaller '<HORUS Test N+1>'
```

O primeiro compila/executa a função NSIS em chave aleatória de fixture: reproduz a
herança original e verifica instalação nova, TEMP removido/raiz/maiúsculas/`..`,
instalação TEMP ainda registrada, local permanente, prefixo semelhante e `/D=`.
O segundo instala N→N+1→N e desinstala `/S`, sem abrir o desktop; compara SHA-256
de SQLite, projeto, preferência e mídia descartáveis fora de `INSTDIR` e limpa
somente a identidade de teste. A fixture de mídia é uma sequência de bytes; esse
teste verifica preservação, não decodificação audiovisual nem migração de schema.
Na HOR-147 foram gerados dois NSIS de teste com o template completo, reutilizando
um desktop compilado e sem recursos backend; esse ciclo valida o instalador,
não a inicialização do aplicativo ou o fluxo completo pelo plugin de atualização.
Relatório local: `%LOCALAPPDATA%\Horus\dev-reports\HOR-147.md`.
