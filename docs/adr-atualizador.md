# ADR — atualizador do desktop Windows (HOR-100)

**Estado: parcialmente decidido em 16/11/2026; implementação não iniciada.** Issue de gestão: `yannbastos/horus-management#88` (HOR-100). Este documento registra as decisões iniciais do usuário, a direção técnica proposta pelo Maestro (ainda não aprovada) e as decisões pendentes. Nada planejado aqui está implementado.

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
