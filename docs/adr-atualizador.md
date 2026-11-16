# ADR — atualizador do desktop Windows (HOR-100)

**Estado: protótipo local autorizado em 16/11/2026; etapa 1 descrita abaixo.** Issue de gestão: `yannbastos/horus-management#88` (HOR-100). As decisões de distribuição e custódia continuam pendentes; não há publicação autorizada.

## Contexto

O Hórus distribui no Windows um aplicativo desktop Tauri 2 com instalador NSIS por usuário (`currentUser`) e backend Python empacotado com PyInstaller (`onedir`), hoje sem mecanismo de atualização automática nem assinatura de código ([empacotamento-windows.md](empacotamento-windows.md)). Em 16/11/2026 foi concluído um estudo comparativo com o projeto MIPE (referenciado em HOR-100), que utiliza `tauri-plugin-updater` com manifesto estático `latest.json`, assinatura minisign e instalação silenciosa automática ao abrir e em segundo plano.

## Decisões do usuário (16/11/2026)

1. Distribuição privada durante o desenvolvimento.
2. Verificação automática de atualizações ao iniciar o aplicativo, com opção de desativar.
3. Download e instalação somente após confirmação do usuário.
4. Nenhuma atualização durante renderizações ou outros trabalhos críticos.
5. Não armazenar credenciais privilegiadas do GitHub no aplicativo.

## Consequências

- Fica descartada a instalação silenciosa automática e o reinício forçado adotados no MIPE.
- O canal de atualização não pode depender de token do GitHub embutido no aplicativo; portanto, não pode consumir GitHub Releases de repositório privado diretamente pelo app.
- A verificação de atualização introduz um novo fluxo de rede no desktop, que exigirá registro de entrada `F-xx` em [inventario-fluxos-dados.md](inventario-fluxos-dados.md) e cobertura na trava estática de rede quando a implementação for autorizada (nenhuma entrada foi criada agora).

## Direção recomendada pelo Maestro (proposta — ainda NÃO aprovada)

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

**Desinstalação não é garantia universal de preservação:** o `installer.nsi` efetivamente
gerado contém exclusão recursiva de `$APPDATA\${BUNDLEID}` e
`$LOCALAPPDATA\${BUNDLEID}` quando `DeleteAppDataCheckboxState = 1` **e** `UpdateMode <> 1`.
`/UPDATE` define `UpdateMode = 1`; a exclusão fica impedida nesse fluxo. Desinstalar com `/S`
não exibe nem marca a caixa; a variável inicia em zero. Não foi ensaiada exclusão interativa
nem se tocou nos dados reais. Decisão pendente do usuário: manter ou remover a caixa;
recomendação: removê-la em futuro incremento, deixando exclusão no fluxo explícito de dados.

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
