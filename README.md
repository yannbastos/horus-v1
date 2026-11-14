# Hórus

**Plataforma de inteligência audiovisual em desenvolvimento.**

Validação local recente: render de rascunho real pelo backend, cancelamento da árvore, persistência e seleção/reprodução de MP4 no Tauri foram exercitados. A árvore desktop foi medida com WebView2 incluído; consulte [resultados e limites](docs/validacao-local-2026-11-10.md). A instalação NSIS e a equivalência completa da transcrição continuam pendentes.

Pesquisar oportunidades, planejar vídeos originais e gerenciar a operação audiovisual. O Hórus está sendo construído para reunir pesquisa audiovisual, planejamento editorial, produção assistida por IA e acompanhamento de canais em um único espaço.

O projeto começa pela operação de um canal no YouTube. A expansão para outros canais e formatos virá depois da validação do fluxo, da qualidade e dos custos.

[Visão do produto](docs/VISION.md) · [Estado e prioridades](docs/ROADMAP.md) · [Arquitetura](docs/ARCHITECTURE.md) · [Sistema de IA](docs/AI-SYSTEM.md) · [Biblioteca de técnicas](docs/MOTION-LIBRARY.md) · [Guias e histórico](docs/planejamento/README.md)

Para agentes: leia [AGENTS.md](AGENTS.md) e use seu mapa de consulta por tarefa. Documentação de visão e planos históricos não autoriza implementar funcionalidades ou repetir autorizações antigas.

**Migração em andamento:** React/TypeScript + Tauri 2, Rust apenas para integrações nativas, Python/FastAPI e SQLite. A prova de conceito local já possui interface web, tarefas persistentes, logs e integração limitada com o CLI da fábrica. O build Tauri e o instalador Windows já foram gerados; abertura e conexão automática ao backend foram verificadas. A transcrição foi portada para a nova interface e o desktop C#/Avalonia legado foi removido (HOR-010; a matriz de equivalência está em [migração do legado](docs/migracao-legado-csharp.md)). Consulte [como executar e os limites da migração](docs/migracao-local.md).

## O que já funciona

[Baixar a prévia Windows](https://github.com/yannbastos/horus/releases/tag/v0.1.0-alpha.1). O instalador inclui o backend; a prévia publicada antecede a revisão mais recente dos textos audiovisuais.

A nova interface desktop usa **React/TypeScript e Tauri 2**, com backend **FastAPI/SQLite**, operações limitadas da fábrica e biblioteca local. A ferramenta de transcrição permite importar áudio e vídeo, baixar um vídeo do YouTube, transcrever localmente com Whisper, revisar e salvar o texto. Há temas claro, escuro e do sistema e testes automatizados de interface e comportamento.

Consulte a [tabela canônica de capacidades e limites](docs/ROADMAP.md#estado-consolidado), que distingue implementado, parcialmente validado e planejado. Render real possui ensaios delimitados registrados; isso não certifica o fluxo editorial completo ou a execução por todos os modos do aplicativo.

Operações limitadas da fábrica e mídia local estão descritas no guia de migração.

## Prévia do desktop audiovisual

![Prévia do layout audiovisual do Hórus em tema escuro](docs/previews/desktop-dark.png)

Captura histórica do layout audiovisual (referência visual). [Capturas e limites do layout](docs/layout-desktop.md).

## Para onde o Hórus está evoluindo

O [escopo funcional](docs/VISION.md#escopo-funcional) reúne os módulos e princípios do produto. Detalhes de orquestração/memória pertencem a [AI-SYSTEM](docs/AI-SYSTEM.md); a biblioteca inteligente planejada é descrita em [MOTION-LIBRARY](docs/MOTION-LIBRARY.md). Esses objetivos não são funcionalidades prontas.

## Como os projetos se conectam

| Componente | Papel |
|---|---|
| **Hórus — este repositório** | Aplicativo e referência central do produto e do planejamento |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Motor de inteligência e orquestração previsto, por integração externa e sem fork inicial |
| [Fábrica de canais](https://github.com/yannbastos/fabrica-canais) | Motor independente Python/CLI e Remotion; não exige o desktop para funcionar |
| [Laboratório visual](https://github.com/yannbastos/horus-storybook) | Componentes e referências de interface em React/Storybook |

A primeira etapa é inteiramente local, sem VPS. Os componentes independentes (fábrica, Hermes) são preservados. Autorizações anteriores foram pontuais e não concedem permissão para novas tarefas. Consulte [o planejamento de integração](docs/planejamento/README.md).

A conexão local com o Hermes CLI está descrita em [integração Hermes](docs/integracao-hermes.md). A verificação usa somente a API de diagnóstico; não envia prompts ou inicia produções.

## Transcrição

Para o motor (faster-whisper) e os detalhes da transcrição, consulte a [migração](docs/migracao-local.md#transcrição). O primeiro download do modelo e a obtenção de vídeos do YouTube exigem internet.

1. Arraste arquivos (mp3, wav, m4a, mp4, mkv, ...) para a tela ou use **Selecionar arquivos**.
2. A fila é FIFO, com **até 20 arquivos incluindo o atual**. O primeiro da fila é o atual.
3. Escolha o modelo e o idioma (ou deixe a detecção automática) e clique em **Transcrever**.
4. Ao concluir, o app **para e mantém o resultado**. Revise, edite e use **Salvar .txt**. **Copiar** não libera a fila.
5. Só depois de salvar com sucesso, clique em **Próximo vídeo**. Editar o texto exige salvar novamente.

Em erro ou cancelamento, o arquivo atual e o texto parcial são preservados e **Transcrever** tenta de novo o mesmo arquivo. A fila existe apenas durante a sessão: salve o texto antes de fechar o app.

**YouTube:** cole o link de um vídeo e clique em **Baixar e adicionar à fila**. O vídeo entra no final da fila, sem substituir o resultado atual. Baixe apenas conteúdo que você tem autorização para salvar.

Dependências externas:

- **ffmpeg** opcional, no `PATH` (`winget install Gyan.FFmpeg`): a transcrição não o exige, mas o download do YouTube o usa para mesclar vídeo e áudio.
- Para o YouTube: **yt-dlp** e **Node.js 22+** (`winget install yt-dlp.yt-dlp` e `winget install OpenJS.NodeJS.LTS`).

Os modelos são baixados na primeira vez para `HORUS_DATA_DIR/models`. A execução usa CPU ou runtime CUDA conforme os recursos e a configuração do ambiente.

As próximas etapas estão em [docs/ROADMAP.md](docs/ROADMAP.md).

## Tecnologias

Stack, estrutura e decisões estão em [ARCHITECTURE](docs/ARCHITECTURE.md). Os comandos de verificação por componente estão em [AGENTS.md](AGENTS.md#testes-e-verificação).

## Pré-requisitos

Versões e instalação completa em [CONTINUAR-EM-OUTRO-PC](docs/CONTINUAR-EM-OUTRO-PC.md): Windows 10 ou 11, Git, Python 3.12 com uv, Node.js 24 LTS e, para o desktop Tauri, Rust com MSVC Build Tools e WebView2.

## Compilar, executar e testar

Comandos por componente em [AGENTS.md](AGENTS.md#testes-e-verificação); como executar o backend e o desktop em [migração local](docs/migracao-local.md).

## Suporte ao Linux

**Suporte atual:** somente Windows. O Linux ainda não é suportado nem testado, em nenhuma das implementações.

**Suporte futuro:** previsto, com atenção especial ao Arch Linux, e dependente de validação própria. A stack (React, Tauri, Python) é multiplataforma, mas já contém integrações específicas do Windows, como Job Objects e o instalador NSIS, que precisarão de equivalentes. Limites e critérios estão em [ARCHITECTURE](docs/ARCHITECTURE.md#direção-arquitetural-aprovada).

## Contribuições

O projeto ainda está na fase de fundação e o processo de contribuição não foi formalizado. Até lá, sugestões e relatos de problemas são bem-vindos.

Antes de alterar o código, leia [AGENTS.md](AGENTS.md) e [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Licença

A licença open source ainda não foi definida.

## Catálogo visual

O laboratório React/Storybook fica em um repositório separado: [horus-storybook](https://github.com/yannbastos/horus-storybook). Ele reúne componentes e telas para avaliar o design antes da adaptação ao aplicativo e ao futuro site.

Na organização local, o catálogo está em `../HORUS Storybook`. Consulte o README daquele repositório para executar e verificar o catálogo. Ele é independente do aplicativo e usa dados fictícios.
