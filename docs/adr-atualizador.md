# ADR — atualizador do desktop Windows (HOR-100)

**Estado: protótipo local autorizado em 16/11/2026; etapas 1 e 2 descritas abaixo.** Issue de gestão: `yannbastos/horus-management#88` (HOR-100). As decisões de distribuição e custódia continuam pendentes; não há publicação autorizada.

## Contexto

O Hórus distribui no Windows um aplicativo desktop Tauri 2 com instalador NSIS por usuário (`currentUser`) e backend Python empacotado com PyInstaller (`onedir`), hoje sem mecanismo de atualização automática nem assinatura de código ([empacotamento-windows.md](empacotamento-windows.md)). Em 16/11/2026 foi concluído um estudo comparativo com o projeto MIPE (referenciado em HOR-100), que utiliza `tauri-plugin-updater` com manifesto estático `latest.json`, assinatura minisign e instalação silenciosa automática ao abrir e em segundo plano.

## Decisões do usuário (16/11/2026)

1. Distribuição privada durante o desenvolvimento.
2. Verificação automática de atualizações ao iniciar o aplicativo, com opção de desativar.
3. Download e instalação somente após confirmação do usuário.
4. Nenhuma atualização durante renderizações ou outros trabalhos críticos.
5. Não armazenar credenciais privilegiadas do GitHub no aplicativo.

### Decisões complementares (16/11/2026, noite)

6. **Canal durante a validação:** pasta local ou de rede indicada pelo usuário, sem hospedagem externa. A arquitetura deve permitir migrar depois para um proxy HTTPS privado com autenticação revogável por instalação.
7. **Ensaios instalados:** somente em VM Windows descartável, com dados fictícios, sem tocar a instalação operacional.
8. **Núcleo × recursos:** autorizados o estudo técnico e a proposta arquitetural; a implementação aguarda aprovação.
9. **Chave minisign:** autorizada a preparação do procedimento de geração e custódia, em [custodia-chave-atualizador.md](custodia-chave-atualizador.md). A chave definitiva só é gerada depois de o usuário aprovar o procedimento e a opção de armazenamento; nenhum serviço externo de armazenamento sem autorização.
10. **Authenticode:** adiado para a distribuição pública. A verificação criptográfica do atualizador continua obrigatória.
11. **Desinstalador:** sem opção de apagar dados pessoais; arquivos e projetos do usuário são preservados (HOR-147).

Estas decisões resolvem os itens 1, 3 e 4 de "Decisões pendentes" abaixo para a fase atual; o item 2 fica em estudo.

## Consequências

- Fica descartada a instalação silenciosa automática e o reinício forçado adotados no MIPE.
- O canal de atualização não pode depender de token do GitHub embutido no aplicativo; portanto, não pode consumir GitHub Releases de repositório privado diretamente pelo app.
- A verificação de atualização introduz um novo fluxo de rede no desktop, que exigirá registro de entrada `F-xx` em [inventario-fluxos-dados.md](inventario-fluxos-dados.md) e cobertura na trava estática de rede quando a implementação for autorizada (nenhuma entrada foi criada agora).

## Direção técnica (protótipo local autorizado; distribuição pendente)

- Adotar o updater nativo do Tauri 2 (`tauri-plugin-updater` + `tauri-plugin-process`) com manifesto estático e pacote assinado via **minisign**, mantendo confirmação explícita, bloqueio durante trabalhos críticos e encerramento limpo do backend antes de instalar.
- Preservar dados do usuário fora da pasta de instalação, manter backup verificável do SQLite antes de migrações e guardar o instalador da versão anterior em disco para possibilitar rollback.
- Separar o **núcleo** (app desktop + backend PyInstaller) do **pacote de recursos grandes** (FFmpeg, Kokoro, runtime Python/Node, fábrica e modelos), evitando downloads da ordem de ~1,9 GB a cada atualização do aplicativo.
- Centralizar a versão do produto em fonte única com sincronização por script e manter a publicação de releases sempre manual.

## Decisões pendentes (em ordem)

1. **Canal privado de atualização sem credencial no app:** definir onde hospedar o manifesto e o pacote assinado. Opções em aberto: host estático privado com URL não divulgada e controle de acesso sem segredo privilegiado; pasta local ou de rede indicada pelo usuário; ou GitHub Releases públicos apenas do núcleo (opção que conflita com a decisão 1 durante o desenvolvimento).
2. **Separação do pacote de recursos:** confirmar se o pacote de recursos grandes será separado do núcleo ou mantido em instalador único.
3. **Custódia da chave minisign:** definir procedimento de geração, guarda e backup offline da chave privada de assinatura.
4. **Authenticode:** decidir pela contratação de certificado pago de assinatura de código agora, depois ou nunca.

## Riscos conhecidos

- **SmartScreen / Microsoft Defender sem Authenticode:** a assinatura minisign valida apenas o pacote dentro do fluxo do updater; sem certificado Authenticode, o primeiro download e a execução do instalador continuam sujeitos a alertas ou bloqueios do Windows.
- **Perda da chave privada minisign:** se a chave for perdida sem backup, instalações existentes deixam de aceitar atualizações assinadas.
- **Migração de schema irreversível sem backup:** atualizar para versão com schema mais novo impede retorno simples para versão anterior sem restaurar backup compatível do SQLite.
- **Licenças no pacote de recursos:** o perfil offline inclui componentes como FFmpeg GPL e Remotion, o que impede distribuição ou hospedagem pública aberta sem revisão prévia de licenciamento.

## Protótipo local — etapa 1

Implementação delimitada: backup verificável, consulta de atividade, versão sincronizada e
ensaio do empacotamento existente. Plugin, comandos Tauri e atualização assinada ficam
para as etapas 2/3. Nenhum schema foi migrado nesta etapa.

### Dados e limites de preservação

`frontend/src-tauri/src/main.rs` usa `app_local_data_dir()/workspace`, com identificador
`com.horus.audiovisual`: no Windows deste operador, o caminho padrão exato é
`C:\Users\yannn\AppData\Local\com.horus.audiovisual\workspace`. `HORUS_DATA_DIR` substitui
esse caminho no backend; não muda o perfil WebView. O backend iniciado fora do desktop
tem outro padrão: `%LOCALAPPDATA%\Horus\workspace`. Não confundir os dois.

| Estado a preservar | Localização e tratamento |
|---|---|
| Banco operacional, WAL/SHM e backups de migração | `workspace/horus.sqlite3`, sidecars e `horus.backup-v*.sqlite3`; backup consistente pela API SQLite, nunca copiar somente o arquivo vivo |
| Radar e sidecars | `workspace/radar/radar-youtube.sqlite3`; snapshot SQLite separado |
| Preferências do backend | `narration-preference.json`, `notifications-state.json`, comentários em `production-comments/` |
| WebView, localStorage e preferências da interface | `%LOCALAPPDATA%\com.horus.audiovisual\EBWebView` por padrão; independente de `HORUS_DATA_DIR`; ensaio força `WEBVIEW2_USER_DATA_FOLDER` temporário |
| Projetos, manifestos, exportações e vídeos | `workspace/productions/`, `workspace/exports/` e caminhos externos escolhidos pelo operador; referências persistidas não equivalem a cópia da mídia |
| Modelos importados | `workspace/models/` ou diretório configurado; modelos empacotados são recursos reinstaláveis, não estado do usuário |
| Backups pré-atualização | Pasta `update-backups/` irmã do workspace; por padrão dentro de `%LOCALAPPDATA%\com.horus.audiovisual` |
| Integrações e segredos | `integrations/` e configuração externa quando indicada; credencial de narração no Windows Credential Manager via keyring, serviço `horus.narration.elevenlabs`, conta derivada do caminho do workspace |

Esses padrões de estado não ficam na pasta de instalação. Caminhos explicitamente escolhidos
pelo usuário podem ser externos: não há promessa de proteger mídia que ele colocar dentro
da instalação. Keyring não é arquivo no workspace e não foi acessado no ensaio.

**Histórico da opção de exclusão no desinstalador:** o `installer.nsi` efetivamente
da validação histórica continha exclusão recursiva de dados, condicionada à caixa
interativa e fora de `/UPDATE`; o modo `/S` não marcava essa caixa.
**Decisão aplicada na HOR-147 (16/11/2026):** a caixa e todo o bloco de exclusão
foram removidos do template NSIS versionado. Desinstalação interativa, silenciosa
e atualização sempre preservam banco, projetos, arquivos e configurações do usuário.
A exclusão de dados fica fora do instalador. Regressão estática cobre configuração,
ausência do controle e ausência de remoção recursiva; ensaio final em VM descartável.

### Primitivas para a etapa 2

- `GET /v1/update/activity`, autenticado: `{busy, active_jobs, active_previews}`. Conta
  todos os jobs `queued`, `running`, `cancel_requested`, sem o limite de 100 de `/v1/jobs`;
  inclui geração de prévia de narração fora do executor. Jobs terminais não bloqueiam.
- `POST /v1/update/backup`, autenticado, sem caminho fornecido pelo cliente: recusa atividade
  com HTTP 409; reutiliza `export_local_data` e `verify_export`, devolvendo
  `{ok, label, sha256, files}`. Falha de cópia/verificação também retorna 409, sem caminho
  privado. O Rust pode chamar o backend que possui, com token local, antes de encerrá-lo.
- `create_update_backup(data_dir)` gera `pre-update-<uuid>.zip`, sem sobrescrita nem remoção
  automática, no formato de exportação já restaurável. Inclui SQLite consistente com WAL,
  Radar, preferências permitidas, manifestos de produções e comentários. Confere hashes e
  integridade; arquivo elegível inválido ou recusado faz falhar a atualização.

Não inclui mídia, vídeos, exports finais, modelos, backups anteriores, perfil WebView nem
segredos/keyring/integrações. Os dados permanecem no lugar durante instalação; o ZIP é
backup do estado estruturado, não recuperação integral do disco. Radar e banco operacional
são snapshots independentes, não uma transação conjunta. Retenção, cópia externa,
criptografia e ampliação para preferências WebView dependem do usuário; recomendação:
nenhuma remoção automática e cópia externa protegida antes de distribuição real. O pacote
contém dados privados e deve receber a mesma proteção do banco.

Consulta de atividade e backup **não reservam** a aplicação. Existe intervalo para iniciar
novo trabalho após a consulta: etapa 2 precisa impedir novas submissões durante instalação,
reconsultar atividade, encerrar o backend e aguardar o processo. Backend indisponível deve
bloquear, nunca ser interpretado como ocioso. Não usar apenas estado da interface.

### Versão e reprodução

Fonte canônica: `frontend/package.json`. `node scripts/product-version.mjs --check` falha
se houver divergência; `node scripts/product-version.mjs 0.1.2` sincroniza manifestos,
overrides `scripts/tauri*.json`, locks e versão do health. SemVer inclui precedência de
prerelease e ignora metadados para comparação. Versão menor/igual exige
`--allow-non-increasing`. Em releases, validar também as restrições específicas de
NSIS e PEP 440; o protótipo usa somente versões estáveis. O override first-video que
estava em `0.1.1` foi alinhado a `0.1.0`; não houve aumento de versão do produto.
Teste: `node --test scripts/product-version.test.mjs` (cópia temporária, sem alterar o repo).

Ensaio mínimo: comandos da seção [Atualização e rollback manual isolados](empacotamento-windows.md),
com diretório de saída fora do repositório. `check-installer-data.py` semeia uma linha
SQLite, projeto JSON, vídeo MPEG-4 sintético de 16×16 e preferência; compara SHA-256 dos
quatro arquivos e `PRAGMA integrity_check` antes/depois das instalações e após desinstalar.
O teste nunca usa Vite/1420. Modelos e transcrição real não fazem parte do perfil mínimo.

### Compatibilidade com Tauri 2 (análise; plugin ainda ausente)

Segundo a [documentação oficial do updater](https://v2.tauri.app/plugin/updater/),
`bundle.createUpdaterArtifacts=true` habilita os artefatos e assinaturas para atualização;
no Windows v2 o artefato é o próprio instalador NSIS com `.sig`, não o ZIP legado de v1.
É necessário assinar no build e configurar a chave pública no app. Não há delta: os
recursos PyInstaller continuam dentro do instalador completo.

O modo `passive` mostra progresso sem interação, `quiet` oculta interface e `basicUi`
requer interação; isso não substitui confirmação de instalação na aplicação. `currentUser`
é o escopo de instalação e não a política de confirmação. Preferir `passive` no protótipo,
após confirmação explícita; a escolha final e o comportamento real ficam para etapa 2/3.

O backend `onedir` mantém DLLs/arquivos em uso. Antes de instalar, chamar `/v1/shutdown`
autenticado, aguardar saída e só então executar o instalador. O Rust atual espera até
5 segundos e pode matar seu filho; o Job Object encerra descendentes ao fechar. Nenhum
modo NSIS resolve sozinho o risco de processo vivo/arquivo bloqueado. Não matar por nome
nem encerrar outra instância do operador. Testar falha de shutdown, cancelamento e corrida
com novo job na etapa 2. O reinício deve respeitar o dono do Job Object.

Durante a validação adicional apareceu um defeito anterior: `/v1/shutdown` com stdin do
processo gerenciado ainda aberto terminava o PyInstaller com `_enter_buffered_busy`
(3221225477). O monitor de EOF foi trocado para `os.read`, sem lock de `BufferedReader`;
a confirmação final usa o executável congelado, preservando a detecção de morte do dono.

`webviewInstallMode=skip` permanece: exige WebView2 previamente instalado e evita download
de runtime no ensaio. Não comprova funcionamento em Windows limpo sem WebView2.
O mínimo foi medido nesta máquina (~16,84 MiB); transcrição e first-video não foram
reconstruídos nesta etapa (evidência anterior: transcrição ~72,90 MiB; first-video é muito
maior e exige recursos/licenciamento). Não habilitar first-video implicitamente no updater.

Guardar o instalador anterior verificado fora de `$INSTDIR`, por exemplo em
`%LOCALAPPDATA%\Horus\update-cache\<versão>\`, com hash e versão, é recomendação para
etapa 2, ainda não implementada. Não baixar automaticamente uma versão antiga. O cache
do protótipo e a chave de assinatura não substituem backup de dados nem Authenticode.

Verificado: build NSIS mínimo N/N+1; ciclo manual N→N+1→N, integridade e preservação
após desinstalar silenciosamente; guardas backend/testes e snapshot aditivo. Não verificado:
atualização pelo plugin, assinatura real, instalação com backend ocupado, recuperação de
falha de energia, Windows limpo, perfis grandes ou migração entre schemas diferentes.
Evidências detalhadas e contagens: relatório local `%LOCALAPPDATA%\Horus\dev-reports\HOR-100-A.md`.

## Protótipo local — etapa 2

Implementado em branch própria sobre `origin/main` `5b65034`, sem publicação ou instalação:

- Rust usa `tauri-plugin-updater =2.14.1`, `semver =1.0.28`, `reqwest =0.13.5`
  (JSON) e `time =0.3.55` (RFC3339). Versões e checksums estão no Cargo.lock e na
  allowlist de supply chain. Não é necessário plugin de processo: no Windows o
  updater lança NSIS e sai; NSIS reinicia via `/R` no modo escolhido.
- Os quatro comandos `update_*` preservam o contrato snake_case e publicam
  `update://state`. Preferência atômica em `<HORUS_DATA_DIR>/updater/preferences.json`
  ou `<app_local_data_dir>/workspace/updater/preferences.json`, padrão `true`;
  configuração sem canal produz `disabled` e nenhuma consulta. A única consulta
  automática ocorre no setup, se os dois indicadores estiverem habilitados.
- Capabilities permanecem sem `updater:*`, `http:*` ou `process:*`: somente Rust
  acessa o plugin. Nenhum endpoint ou chave consta do build padrão. O protótipo
  só aceita canal HTTP **explicitamente** em `127.0.0.1`, com chave pública,
  `dangerousInsecureTransportProtocol` e `requireSignedVersion=true` no overlay;
  recusa credenciais na URL, certificados/hostnames inválidos, proxy e redirects.
  Não é configuração de distribuição: HTTPS/canal privado continuam pendentes.
- `update_install` serializa operações e compara confirmação/versão antes de efeitos;
  exige backend possuído disponível e ocioso, reserva, reconsulta e backup verificado.
  Falhas interrompem a sequência antes do download/instalação correspondente.
  Download do plugin valida minisign **e a versão no comentário assinado**;
  erros de assinatura são distintos de rede e não executam instalador.
- Reserva autenticada em memória (`POST /v1/update/reservation`, liberação por
  `DELETE`) bloqueia novas mutações da API e recusa requisições mutáveis já em curso;
  leituras, backup e shutdown continuam disponíveis. O worker existente é coberto
  pela reconsulta de todos os jobs, incluindo fila/cancelamento/prévia. Nenhuma
  migração de schema. A reserva é liberada em erros normais e some ao reiniciar o
  backend; falha de comunicação ao liberar exige reiniciar o backend possuído.
- Antes do NSIS, faz `/v1/shutdown` autenticado e aguarda até 15 segundos por saída
  com sucesso do processo possuído, fechando seu Job Object. Falha/timeout impedem
  instalação. Uma guarda independente da fase visual impede `backend_connection`
  de iniciar outro backend durante a operação. Não mata processos por nome.

### Instalação Windows e recuperação

Modo escolhido: **passive**, após confirmação humana; mostra progresso e evita
perguntas redundantes do instalador. A implementação Windows do plugin fixado
`tauri-plugin-updater-2.14.1/src/updater.rs` acrescenta `/UPDATE`, `/P` e `/R` para
NSIS, aguardando o fim do desktop antes de sobrescrever. `/UPDATE` impede o bloco
de exclusão de dados no template NSIS então vigente; na HOR-147 esse bloco foi removido. Referência:
[updater oficial](https://v2.tauri.app/plugin/updater/).
Isso foi conferido no código do plugin; a instalação real é a etapa 3.

Cada instalador **baixado e validado** fica em
`workspace/updater/installers/<versão>/setup.exe` com `setup.exe.sig`, sem remoção
automática. Portanto o instalador da versão atual fica disponível como anterior
após ela ter sido recebida por esse fluxo. **A primeira instalação não fornece
automaticamente uma cópia de seu próprio instalador**: para esse primeiro rollback,
o operador precisa guardar o NSIS original fora da instalação, junto do hash.
O cache está no mesmo disco e não substitui cópia separada ou backup de dados.
O ZIP estruturado da etapa 1 não inclui `updater/` (preferência e cache); esses
arquivos permanecem no lugar durante instalação, e sua inclusão no backup pode
ser decidida com a ampliação da cobertura.
Versões anteriores permanecem para recuperação **manual**; não há downgrade de
schema nem restauração automática, e o updater continua recusando versões <= atual.
Retenção e inclusão de WebView/segredos no backup não foram decididas nesta etapa.

### Scripts para a etapa 3

```powershell
$env:UV_NATIVE_TLS = '1'
$env:NODE_OPTIONS = '--use-system-ca'
powershell -NoProfile -File scripts/build-updater-test.ps1 -Version 0.1.1 -Port 19420
& backend/.venv/Scripts/python.exe scripts/serve-updater-test.py `
  "$env:LOCALAPPDATA/Horus/dev-updater-test/channel" --port 19420
```

O build gera chave **exclusiva de teste** se faltar, em
`%LOCALAPPDATA%\Horus\dev-updater-test\horus-updater-test.key`, descarta a saída do
gerador (que pode conter a chave) e nunca reutiliza credencial de produção.
Chave privada/senha não entram em relatório, log ou Git; o script restaura as
variáveis de assinatura anteriores. O overlay gerado fica fora do repositório,
habilita `createUpdaterArtifacts`, assinatura vinculada à versão e canal local;
manifesto/NSIS/assinatura ficam em `channel/`, sem mudar versões versionadas.
O servidor só escuta loopback e só serve `latest.json` e artefatos versionados;
não serve listagens nem arquivos de chave. Portas 1420 e 8765 são recusadas.
Preservar um canal por ensaio e não executar dois builds sobre o mesmo overlay.

Verificado nesta etapa: testes Rust da máquina de estados/preferência/guardas,
ordem reserva→reconsulta→backup→download/assinatura→cache→shutdown→instalação com
backends simulados; reserva real em bancos temporários; snapshot aditivo e
travas de rede/supply chain; frontend e testes da UI em porta alternativa;
compilação padrão sem canal e geração local de NSIS assinado de teste.
**Não verificado:** instalação pelo updater N→N+1, reinício real, arquivos bloqueados
durante instalação, antivírus/Windows limpo, queda de energia, recuperação e
rollback instalado. Não executar esses passos até a etapa 3.
Relatório e contagens: `%LOCALAPPDATA%\Horus\dev-reports\HOR-100-B.md`.

## Protótipo local — etapa 3

Ensaio Windows de 16/11/2026, sobre `origin/main` `346dfa5`, com N=`0.1.0` e
N+1=`0.1.1`, perfil mínimo e chave exclusiva de teste fora do Git. A versão do
produto nos manifestos versionados continua `0.1.0`. Não houve publicação.

O script [test-updater-e2e.ps1](../scripts/test-updater-e2e.ps1) instala N em
TEMP, controla **o WebView real instalado** por CDP local e confirma pelo diálogo
de Ajustes → Atualizações. Não usa mocks de IPC nem troca o instalador manualmente
no caminho de sucesso. O servidor registra cada requisição: o caso positivo tem
zero GET do pacote antes da confirmação; downloads dos dois casos negativos são
identificados separadamente. Depois da confirmação, foi observado NSIS com
`/P /UPDATE /R /ARGS`, reinício em N+1, backend autenticado saudável e SQLite íntegro.
O instalador baixado/guardado tem o mesmo SHA-256 do artefato assinado do canal.

### Critérios S1–S6

| Critério | Evidência executada |
|---|---|
| S1 — autoridade do backend | Chamadas diretas ao comando Rust com UI livre: job `factory.profile` realmente em execução e reserva previamente negada devolvem `blocked/job_running`; nenhum pacote é pedido. A fábrica é uma fixture local que dorme, não uma renderização/inferência real. |
| S2 — WAL e restauração | Escritor SQLite externo concorrente durante o backup; ZIP verificado e restaurado, conteúdo e `integrity_check` conferidos; o backend N+1 instalado abre a cópia restaurada e responde à saúde autenticada. Mídia/cache excluídos do ZIP são preservados/copiados separadamente no ensaio, explicitamente. |
| S3 — compatibilidade | Manifestos menor e igual nunca viram `available`; retorno manual a N apenas com instalador guardado e schema compatível. Cópia sintética de schema futuro é recusada pelo backend antigo, com hash inalterado. |
| S4 — concorrência | Consultas e instalações simultâneas devolvem o estado ocupado à segunda chamada: uma consulta ao manifesto ou um download/backup, sem duplicação de reserva/efeitos. |
| S5 — assinatura | Pacote com byte adulterado e pacote assinado por outra chave geram `signature_invalid`; hash do binário, conteúdo lógico de todas as tabelas SQLite e quatro arquivos de fixture permanecem inalterados. |
| S6 — sucesso após saúde | Recibo pendente antes de instalar; no reinício, fase `installing` até validar versão nova + backend autenticado + `PRAGMA integrity_check`. Só então grava resultado saudável e volta a `idle`. Banco futuro provoca resultado não saudável e `install_failed`, nunca sucesso. |

Não foi necessária fase nova no contrato. `GET /v1/update/health` é somente
leitura/autenticado, adicionado ao snapshot OpenAPI; não modifica schema.
Metadados do resultado ficam em `workspace/updater/pending-health.json` e
`last-update-result.json`, sem segredos. Falha mantém o recibo para diagnóstico
e nova verificação ao reiniciar; não instala versão menor automaticamente.

### Isolamento e defeitos encontrados

O relançamento NSIS passa pelo shell; o ensaio não depende de herdar env. O overlay
`-E2eRoot` fixa `appDirectoriesOverride` e CDP, e o Rust só reconhece esse sandbox
quando o canal local é válido e a pasta exclusiva está sob TEMP. Backend, Hermes
fictício, preferências e WebView permanecem no sandbox após reiniciar. O build
padrão não recebe esse overlay nem CDP.

Corrigidos durante o ensaio:

- `canonicalize()` acrescentava `\\?\` no Windows; ao chegar ao Python, a URI
  SQLite tinha autoridade `%3F` e o backup era recusado. A validação usa o canônico,
  mas o caminho propagado permanece absoluto normal; regressão Rust adicionada.
- Fechar a última janela após falha de inicialização do backend podia deixar o
  desktop vivo. O encerramento agora solicita saída explícita também sem backend,
  sem interceptar novamente a saída programática; o caso real de saúde negativa
  cobre essa regressão e termina sem processo órfão.

A primeira tentativa abortou corretamente porque o contador da pasta real variou
19 bytes; medições posteriores, com o ensaio já desinstalado, também variaram.
O app real não foi encerrado pelo ensaio. A execução final ocorreu numa janela
em que ele já não era encontrado e confirmou contagem/tamanho idênticos antes/depois,
desinstalação `/S`, registro/atalhos limpos e ausência de processos de teste.
Nenhum dado real foi restaurado, revertido ou removido para satisfazer o contador.

### Repetição e roteiro manual

```powershell
$env:UV_NATIVE_TLS = '1'
$env:NODE_OPTIONS = '--use-system-ca'
powershell -NoProfile -File scripts/test-updater-e2e.ps1 -Build
```

O script preserva seu sandbox e evidências; reexecuções arquivam somente os arquivos
de ensaio anteriores. Sem `-Build`, informe `-Root` correspondente ao overlay já
compilado. Recusa instalação preexistente e não usa portas 1420/8765. Canal em
19430, CDP em 19431; testes de UI auxiliares usam 19432.

Para repetir clicando: construir N e N+1 com `build-updater-test.ps1`, mesmo
`-E2eRoot` temporário, porta e chave; guardar N, instalar N no destino TEMP e abrir
com dados/perfil isolados; servir `channel-c` em 127.0.0.1; em **Ajustes → Atualizações**,
clicar **VERIFICAR AGORA**, conferir N+1 e abrir o diálogo de instalação; cancelar
deve manter zero downloads positivos; confirmar deve criar backup, baixar/verificar,
fechar/reabrir e mostrar N+1, com recibo saudável e dados íntegros. Usar outro usuário/VM
se houver instalação existente e não executar sobre a instalação operacional.

Rollback continua **manual**: fechar N+1, guardar banco/sidecars e instalador atual,
reinstalar N somente quando seu schema for compatível ou restaurar backup verificado
em pasta nova. O ZIP não restaura mídia, modelos, WebView, segredos nem `updater/`;
esses recursos permanecem separados. O ensaio N/N+1 usa schema 9 em ambos, não uma
migração real; a recusa de schema 10 foi exercitada numa cópia descartável.

Verificado: jornada instalada pelo plugin, confirmação real, todos os negativos,
WAL/restauração/saúde/rollback compatível e limpeza. Não verificado: máquina limpa,
queda de energia, atualizações com migração real, canal remoto ou assinatura
Authenticode. Distribuição, custódia da chave real, retenção/cobertura do backup e
política de exclusão de dados fora do instalador continuam decisões do usuário;
a caixa do desinstalador foi removida na HOR-147.
Contagens, hashes, comandos, screenshots e logs ficam no relatório local
`%LOCALAPPDATA%\Horus\dev-reports\HOR-100-C.md`.

### Correção do isolamento dos instaladores — HOR-147

Os builds de ensaio agora usam `productName=HORUS Test` e
`identifier=com.horus.installer-test`, inclusive o baseline sem canal.
Isso separa chaves de produto/Uninstall e atalhos da instalação operacional.
`test-updater-e2e.ps1` e `updater-e2e.mjs` recusam artefatos cujo VERSIONINFO
não seja `HORUS Test`; todos os artefatos antigos devem ser reconstruídos.
As guardas de registro e a limpeza referem-se exclusivamente ao produto de teste.
O overlay `-E2eRoot` continua obrigatório para isolar dados/WebView após relançar.

O template NSIS mantém locais personalizados permanentes, ignora apenas a lembrança
TEMP sem instalação registrada e interrompe se houver instalação registrada em TEMP,
exigindo decisão explícita de migração. Ver procedimento e proposta não aplicada em
[empacotamento-windows.md](empacotamento-windows.md#diretório-inicial-e-isolamento-de-ensaios-hor-147).
A HOR-147 validou NSIS N→N+1→N e desinstalação silenciosa com identidade isolada,
SQLite e arquivos externos preservados por hash; não repetiu a jornada CDP/plugin,
assinatura, inferência ou primeiro vídeo. A evidência histórica HOR-100-C permanece
válida para a versão ensaiada, sem homologar automaticamente os novos overlays.
