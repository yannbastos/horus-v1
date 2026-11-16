# Perfil offline de primeiro vídeo — HOR-143

Perfil privado para Windows x64, adicional ao instalador mínimo e ao perfil de
transcrição. Seu runtime fica em `backend/first-video`, relativo ao backend
instalado. Nenhum caminho da máquina de build é configurado no aplicativo.
Não há download, instalação de dependências ou seleção automática de provedor pago
durante produção. WebView2 e navegador Edge preexistentes continuam necessários.

## Recursos e procedência

`scripts/first-video-artifacts.json` fixa versões, URLs e SHA-256 dos artefatos
binários e pesos. Python é a distribuição relocável CPython 3.12.16 do
[python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110),
com digest publicado na release upstream. Node 22.23.5 é obtido de
[nodejs.org](https://nodejs.org/dist/v22.23.5/SHASUMS256.txt).
`scripts/first-video-requirements.txt` fixa com hashes todas as dependências Python,
incluindo Kokoro 0.9.4 e PyTorch 2.8.0 CPU; não redistribui CUDA.
O runtime exclui bibliotecas estáticas `.lib`, símbolos `.pdb` e os headers de
compilação do PyTorch; preserva as DLLs de inferência e os avisos de licença. A wheel
é verificada antes dessa seleção. Não há compilação/JIT de extensões neste perfil.

A fábrica permanece independente: o build exporta somente código e locks da revisão
fixada, sem canais, mídias, bancos, segredos ou ambientes do operador. Remotion e
dependências npm são instalados pelo `package-lock.json` exportado, com integridade
do registry e scripts de instalação desativados. Cada arquivo resultante fica
inventariado por SHA-256 no `runtime.json`; esse inventário serve à verificação do
build e não é uma assinatura digital nem uma barreira contra adulteração por quem
possa modificar a instalação.

Kokoro inclui apenas pesos/configuração e as vozes pt-BR `pf_dora`, `pm_alex` e
`pm_santa` da revisão HF fixada. Os hashes continuam sendo conferidos pelo adaptador
antes da síntese. Whisper small pode ser importado por **Ajustes → Transcrição**,
usando a função existente de importação local, que também atende o alinhamento.
Pesos Whisper não são baixados pelo job e não fazem parte deste runtime.

## Licenças e limites de distribuição

Esta entrega é um instalador privado para uso individual do operador, sem release
pública. Não escolhe a licença do Hórus nem da fábrica e não autoriza republicação
do pacote. Licenças originais dos pacotes Python e npm permanecem junto dos arquivos;
`licenses/` contém avisos, locks, lista de pacotes npm e as licenças principais.

| Componente | Licença / condição | Evidência incluída |
| --- | --- | --- |
| Python | PSF e avisos de terceiros da distribuição | `licenses/Python.txt` e distribuição original |
| Node.js | MIT e avisos dos componentes incorporados | `licenses/Node.txt` |
| Kokoro código/pesos | Apache-2.0 | `licenses/Kokoro-Apache-2.0.txt`, revisão e hashes do modelo |
| PyTorch CPU | BSD e avisos de terceiros | licenças da wheel em `torch`/`torch-*.dist-info` |
| FFmpeg BtbN n8.1.3-35-gf1acb0a266, GPL, version3 | GPL-3.0-or-later conforme configuração deste build; bibliotecas incorporadas têm seus avisos próprios | `licenses/FFmpeg-GPL.txt`, `licenses/FFmpeg-build.txt`, hashes dos executáveis |
| espeak-ng / phonemizer-fork | GPL-3.0 / GPL-3.0-or-later segundo upstream/metadados | arquivos e metadados das wheels, isolados no processo Python externo |
| Remotion 4.0.538 | Remotion License; uso individual gratuito nas condições upstream, não MIT/Apache | licença original nos pacotes e lock npm |
| Archivo Expanded | SIL OFL | `fonts/OFL-Archivo.txt` |

Fontes para consulta das condições: [Python](https://docs.python.org/3/license.html),
[Node](https://github.com/nodejs/node/blob/main/LICENSE),
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M),
[FFmpeg](https://ffmpeg.org/legal.html),
[BtbN](https://github.com/BtbN/FFmpeg-Builds),
[espeak-ng](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING),
[Remotion License](https://www.remotion.dev/docs/license) e
[condições Remotion](https://www.remotion.dev/docs/terms).
Os fontes de FFmpeg e bibliotecas usadas pelo builder devem acompanhar qualquer
distribuição a terceiros ou ter sua forma de disponibilização conforme a GPL
revisada antes dela; não se presume que um link upstream isolado cumpra essa obrigação.

Licença do motor/pesos não concede automaticamente direitos comerciais sobre toda
saída. O treino Kokoro inclui dados sintéticos e fontes com atribuição; a aplicação
mantém `publishable=false`, evidência de procedência e revisão humana. Aceite
subjetivo da voz e verificação das licenças dos assets permanecem humanos.

## Build

Preparar com `scripts/prepare-first-video.py`, informando fontes de build locais
da fábrica na revisão fixada e do FFmpeg com os hashes fixados. Pesos Kokoro podem
ser reutilizados de uma pasta local verificada ou obtidos da revisão HF fixada.
O stage é criado em pasta de cache exclusiva, nunca sobre ferramentas operacionais.
Builds posteriores verificam seus hashes e reutilizam o stage; mudança no lock exige
uma nova pasta, preservando a anterior.

O perfil Tauri `scripts/tauri-first-video.json` chama o build com transcrição e
runtime offline. As variáveis `HORUS_BUILD_FACTORY_SOURCE`,
`HORUS_BUILD_FFMPEG_SOURCE`, `HORUS_BUILD_KOKORO_SOURCE` e
`HORUS_BUILD_FIRST_VIDEO_STAGE` são entradas de build, nunca exigências para abrir
o aplicativo instalado. O perfil mínimo continua usando `scripts/build-backend.ps1`
sem os switches adicionais.
