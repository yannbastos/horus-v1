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

Não inclui faster-whisper/CTranslate2/PyAV, Kokoro, yt-dlp, FFmpeg, Node,
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
sintética em `media` e um arquivo; verifica ambos e `PRAGMA integrity_check`
antes e depois de iniciar cada reinstalação. Os smokes autenticados verificam
health, diagnostics e ausência de backend órfão após fechamento normal e queda.
A desinstalação ocorre em `finally`, inclusive após falha do ensaio.

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
