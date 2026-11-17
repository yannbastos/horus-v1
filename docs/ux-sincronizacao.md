# HOR-137 (Fase A) — Impacto no Frontend, Sincronização e UX

**Issue:** `#124` (HOR-137) — `yannbastos/horus-management`
**Fase:** A (Diagnóstico e especificação; **sem código de produção**, sem migração de dados, sem remoção de SQLite)
**Restrições preservadas:** fluxo do primeiro vídeo intocado; produção local 100% funcional offline; nenhum segredo no frontend ou no Git; sem redesign visual; linha Hermes retida (`integ-hermes-line-v3`) não integrada.

---

## 1. Diagnóstico do Estado Atual (React + Tauri)

Hoje o frontend em `frontend/src/` opera exclusivamente contra o backend FastAPI local em loopback (`http://127.0.0.1:<porta>`):

- **Descoberta e conexão (`frontend/src/platform/desktop.ts`, `frontend/src/App.tsx`):** no desktop Tauri 2 (`nativeDesktop === true`), `connectDesktop()` invoca o comando nativo `backend_connection` e valida `GET /health`. No navegador (`Prévia no navegador`), a conexão é manual em `Ajustes → GERAL` (`Settings.tsx`).
- **Indicadores globais atuais (`frontend/src/App.tsx`):**
  - `.topbar`: exibe trilha `HORUS / CENTRAL AUDIOVISUAL` e a tag `APLICATIVO EM DESENVOLVIMENTO`.
  - `.sidebar-bottom`: exibe status do `HERMES AGENT` (`.status-dot` + texto) e acesso a `Atalhos` e `Ajustes`.
  - `<footer>`: exibe o estado do backend local (`Backend local conectado`, `Preparando backend local…`, `Prévia no navegador` ou `Backend indisponível: ...`).
  - Banner `.production-notice`: aviso contextual no topo da área principal para eventos de produção (`succeeded`, `interrupted`, `failed`), com botões de ação e fechamento.
- **Componente padrão de mensagens (`frontend/src/components/StatusMessage.tsx`):** `StatusMessage` padroniza `state` (`'loading' | 'error' | 'empty' | 'success'`), `role` (`'alert'` para erro, `'status'` para carregamento/sucesso) e `ariaLive` (`'polite'` por padrão em `loading`).
- **Tokens e estilos (`frontend/src/styles/tokens.css`, `frontend/src/styles/app.css`):** cantos retos (`--RadiusNone: 0px`), sem sombras (`--ShadowNone`), fontes locais (`Archivo`, `Inter`, `Geist`), estados semânticos `--Good`, `--Warn`, `--Bad`, `--AccentInk`, `--Muted`, `--Sunken`, com suporte obrigatório aos temas `dark` e `light`.

**Lacuna para sincronização opcional em nuvem (Supabase):** não existe hoje no frontend nenhum conceito de identidade de usuário, estado de conectividade externa, fila de sincronização (`pendente`), estado sincronizado (`sincronizado`), resolução de conflito ou erro de autenticação de nuvem.

---

## 2. Princípios de Arquitetura UX (Local-First e Privacidade)

1. **Local-First absoluto:** SQLite e o armazenamento local (SSD / DAS) continuam sendo a fonte primária de operação na interface. A nuvem é uma camada opcional de continuidade/backup de metadados aprovados, nunca pré-requisito para abrir o app, transcrever, gerar roteiro, narrar ou renderizar.
2. **Zero segredos no frontend:** o React/Tauri **nunca** armazena ou recebe `service_role`, connection strings do PostgreSQL ou chaves administrativas. Toda comunicação de sincronização passa pelo backend FastAPI local (ou fluxo OAuth/PKCE seguro intermediado por ele, caso a arquitetura aprove login individual).
3. **Sem polling em repouso:** alinhado a `docs/ARCHITECTURE.md` (Desempenho e ciclo de vida), o frontend não deve manter polling contínuo em background para checar nuvem. O backend emite eventos ou responde a transições explícitas (ex.: ao iniciar sessão, ao detectar retorno de rede, ou por ação manual `SINCRONIZAR AGORA`).
4. **Fluxo do primeiro vídeo intocado:** nenhuma tela de login bloqueante (`login wall`) na inicialização. Quem abre o Hórus pela primeira vez vai direto para `Visão geral` / `Central de Produção` e executa o piloto local sem conta externa.

---

## 3. Modelo de Dados na UI: `local` vs `sincronizado` vs `pendente`

Cada entidade elegível à sincronização passa a carregar (na camada de visualização) um metadado de origem/estado de sincronização, sem misturar mídia binária pesada (vídeos, áudios, renders) com metadados leves:

| Classificação na UI | Significado | Exemplos de itens | Comportamento visual |
|---|---|---|---|
| **Somente local** (`local`) | Existe apenas neste computador; não sincroniza por escolha de privacidade ou por ser arquivo binário pesado | Mídia bruta na `Biblioteca`, modelos Whisper/Kokoro, arquivos temporários, produções marcadas como locais | Tag neutra `.tag` (`LOCAL`), sem tentativa de envio |
| **Sincronizado** (`sincronizado`) | Metadados locais e cópia remota alinhados na mesma revisão | Projetos/roteiros/decisões editoriais após envio confirmado | Tag discreta `.tag.good` (`SINCRONIZADO`) apenas quando sincronização estiver ativa |
| **Pendente** (`pendente`) | Alteração salva localmente no SQLite aguardando envio (offline ou na fila) | Aprovação editorial feita offline, edição de roteiro, comentário de revisão | Tag `.tag.warn` (`PENDENTE`) com indicação de que está seguro localmente |
| **Conflito** (`conflito`) | Alteração local e alteração remota divergem na mesma revisão | Roteiro ou decisão editada em dois dispositivos antes de sincronizar | Tag `.tag.bad` (`CONFLITO`) + ação explícita de resolução humana |

> **Regra de limpeza visual:** quando o usuário **não** tiver sincronização em nuvem habilitada (modo padrão offline/local), as listas não devem exibir ruído repetitivo de `LOCAL` em todos os itens; a indicação global (`Modo local · Sem conta conectada`) é suficiente. As tags por item só aparecem quando a sincronização estiver configurada ou houver item `PENDENTE` / `CONFLITO`.

---

## 4. Estados Globais de Conexão e Sincronização

A máquina de estados global da camada de sincronização (independente do estado `Backend local conectado`, que continua sendo o indicador primário de saúde do aplicativo):

| Estado | Condição | Cor / Token | Rótulo curto (Shell) | Impacto na operação local |
|---|---|---|---|---|
| `offline` (ou `local`) | Sem internet, modo offline ativo, ou conta não vinculada | `--Muted` (sem conta) / `--Warn` (sem rede com pendências) | `Offline · Local ativo` (ou `Modo local`) | **Nenhum bloqueio.** Produção, transcrição e revisões continuam 100% locais. Alterações elegíveis entram na fila local. |
| `online` | Conta autenticada, rede disponível, zero pendências e zero conflitos | `--Good` (`#3FD6B8` dark / `#0A7563` light) | `Sincronizado` | Operação normal; alterações enviadas conforme política do backend. |
| `sincronizando` | Envio/recebimento em curso no backend | `--AccentInk` (`#AAA5FF` dark / `#4939B8` light) | `Sincronizando…` (com contagem opcional: `Sincronizando (3)…`) | Interface não trava; usuário continua navegando e produzindo localmente. |
| `conflito` | Um ou mais itens apresentam divergência de revisão entre local e remoto | `--Bad` (`#FF6259` dark / `#B3261E` light) | `Conflito (N)` | Itens sem conflito continuam operando; item em conflito aguarda escolha humana antes de sobrescrever. |
| `erro_autenticacao` | Sessão expirada, revogada ou token rejeitado | `--Bad` (`#FF6259` dark / `#B3261E` light) | `Sessão expirada` | **Operação local preservada.** Sincronização pausa com segurança; pendências continuam salvas no SQLite até novo login. |

---

## 5. Indicadores de Sincronização (Global e por Item)

### 5.1 Indicador Global (sem redesign)
Para preservar a barra lateral de `216 px` e a hierarquia descrita em `docs/ARCHITECTURE.md`:

- **Localização primária — `footer` (`App.tsx`):** o rodapé já exibe à esquerda o estado do backend local (`Backend local conectado`). Propõe-se exibir logo ao lado (separado por `·`) o estado resumido de sincronização com `.status-dot` (`.good`, `.bad` ou padrão `--Warn`) + texto curto clicável que leva a `Ajustes → Privacidade e Segurança` (ou abre o painel de sincronização), mantendo a segunda coluna informativa à direita.
- **Localização secundária de alerta — Banner contextual (padrão `.production-notice`):** exibido **apenas** nos estados acionáveis `conflito` ou `erro_autenticacao`, usando a estrutura já existente de `.production-notice` (`role="status"` ou `role="alert"`), com tag semântica (`.tag.warn` / `.tag.bad`), mensagem objetiva em português e botões `Resolver conflito` / `Entrar novamente` e `Dispensar`.
- **Nenhuma alteração na `.topbar`:** mantém `HORUS / CENTRAL AUDIOVISUAL` e `APLICATIVO EM DESENVOLVIMENTO` intactos para não quebrar testes visuais e de layout existentes.

### 5.2 Indicador por Item
Nos cards e linhas de tabela onde há entidades sincronizáveis (`Central de Produção`, `Produção`, `Biblioteca → Projetos importados`), reutilizar exclusivamente a classe `.tag` já existente em `app.css`:

- `.tag.good` → `SINCRONIZADO`
- `.tag.warn` → `PENDENTE`
- `.tag.bad` → `CONFLITO`
- `.tag` (neutra) → `SOMENTE LOCAL`

**Regra de tipografia e acessibilidade:** usar `Geist Mono` (`--FontMono`, `--TextXs`, `letter-spacing: 1.2px`, definido em `.tag`), nunca depender apenas de cor (sempre texto explícito dentro da tag e `aria-label` descritivo quando houver tooltip/data da última sincronização). **Não** inserir badges dentro da área de leitura de roteiro ou transcrição (`Inter`) para preservar legibilidade.

---

## 6. Mensagens de Erro e Recuperação em Português

Todas as mensagens seguem o contrato de `StatusMessage.tsx` (`state`, `role`, `ariaLive`) e o padrão do `ErrorCenter.tsx` (causa clara + garantia de preservação local + próximo passo):

| Cenário | `StatusMessage` (`state` / `role`) | Título / Texto proposto | Ação de recuperação na UI |
|---|---|---|---|
| **Sem internet com alterações pendentes** | `state="empty"` (`tone="muted"`, `role="status"`) | `Sem conexão com a nuvem. Suas alterações estão salvas neste computador e serão enviadas quando a rede voltar.` | Botão secundário `Tentar sincronizar` (habilitado quando houver rede) |
| **Sessão expirada / 401** (`erro_autenticacao`) | `state="error"` (`role="alert"`) | `Sua sessão na nuvem expirou. O trabalho local continua disponível, mas a sincronização foi pausada.` | Botão primário `Entrar novamente` + botão `Continuar offline` |
| **Conflito de revisão** (`conflito`) | `state="error"` (`role="alert"`) | `Este item foi alterado aqui e na nuvem. Escolha qual versão manter antes de continuar a sincronização.` | Botão primário `Comparar e resolver` |
| **Falha temporária do servidor / 5xx / timeout** | `state="error"` (`role="alert"`) | `Não foi possível sincronizar agora devido a uma instabilidade externa. Seus dados locais permanecem intactos.` | Botão `Tentar novamente` (com backoff no backend, sem loop automático na UI) |
| **Cota ou limite do projeto remoto** | `state="error"` (`role="alert"`) | `A sincronização atingiu o limite disponível na nuvem. Seus arquivos e produções locais não foram afetados.` | Botão `Ver detalhes` (leva a `Ajustes → Privacidade e Segurança`) |
| **Backend local desconectado** | `state="error"` (`role="alert"`) | `Backend local indisponível. Conecte o backend antes de alterar opções de sincronização.` | Botão `Reconectar backend` (fluxo já existente em `Settings.tsx`) |

---

## 7. Fluxo de Login Individual (se aprovado pela arquitetura)

Caso a decisão arquitetural da Fase A aprove autenticação individual de operador para sincronização opcional:

1. **Ponto de entrada único e opcional:** em `Ajustes → GERAL` (novo card `CONTA E SINCRONIZAÇÃO`, abaixo de `CONEXÃO LOCAL` e antes de `AMBIENTE LOCAL`, ou integrado à aba `PRIVACIDADE E SEGURANÇA` sob `PROCESSAMENTO E REDE`). **Nunca** na tela inicial ou bloqueando a navegação.
2. **Mecanismo seguro (sem senha em texto claro no React):**
   - O usuário clica em `CONECTAR CONTA`.
   - O frontend solicita ao backend local o início do fluxo OAuth/PKCE (ou magic link / código de dispositivo) e abre o navegador padrão do sistema via Tauri (`shell`/`opener`) para autenticação segura no provedor, retornando via callback loopback controlado pelo backend.
   - O frontend recebe apenas o estado da sessão (`autenticado`, `identificador mascarado`, `expira_em`), nunca armazenando tokens sensíveis em `localStorage` ou código-fonte.
3. **Estado pós-login:**
   - O card passa a exibir `Conta conectada`, email mascarado ou identificador do operador, última sincronização (`Última sincronização: dd/mm/aaaa hh:mm`), seletor explícito de modo (`Sincronização automática quando online` vs `Somente manual`) e botões `SINCRONIZAR AGORA` e `DESCONECTAR`.
4. **Desconexão (`DESCONECTAR`):**
   - Encerra a sessão remota com confirmação simples; **nunca** apaga dados do SQLite local nem mídias do disco.

---

## 8. Como a Produção Local Continua Offline

Para garantir o princípio *Local-First* e manter o fluxo do primeiro vídeo intocado:

1. **Desacoplamento total do `JobManager`:** criação de produção (`POST /v1/productions`), validação de roteiro, geração de áudio (Piper, Kokoro, Windows SAPI), transcrição (Whisper), alinhamento, preview e render final (FFmpeg/Remotion) consultam e gravam apenas no SQLite e sistema de arquivos locais.
2. **Fila de saída não bloqueante (`outbox`):** quando o usuário aprova um roteiro, conclui uma revisão ou edita metadados estando `offline` ou com `erro_autenticacao`, a UI conclui a ação localmente na hora (feedback imediato com `StatusMessage state="success"`) e marca o item como `PENDENTE` apenas se a sincronização estiver ativa.
3. **Exportação e importação `.zip` preservadas:** o fluxo offline de portabilidade de projetos (`Library → Projetos importados`) continua funcionando sem dependência de rede.
4. **Regra de ouro no código futuro:** nenhuma chamada de UI para `ProductionCenter`, `ProductionRun`, `Transcription` ou `Library` pode aguardar resposta da nuvem (`await sync`) para liberar botões locais.

---

## 9. Impacto por Tela Existente (Sem Redesign)

| Tela / Componente | Arquivo atual | O que muda (mínimo necessário) | O que NÃO muda |
|---|---|---|---|
| **Shell Global** | `frontend/src/App.tsx` | Adicionar indicador discreto de sincronização no `footer` ao lado do status do backend local; exibir banner padrão `.production-notice` apenas em `conflito` ou `erro_autenticacao`. | `.topbar`, largura da `.sidebar` (`216 px`), itens de navegação, atalhos globais, comportamento do `NotificationBellButton`. |
| **Visão Geral** | `frontend/src/components/Sections.tsx` | **Nenhuma mudança estrutural.** `ReadinessPanel` continua verificando prontidão local. | Hero, `Orbit`, cards de etapas, CTA `ABRIR PRODUÇÃO`. |
| **Central de Produção** | `frontend/src/features/ProductionCenter.tsx` | Adicionar coluna/tag discreta `.tag` por linha na `CenterTable` (`SINCRONIZADO`, `PENDENTE`, `CONFLITO`) **somente** quando sincronização estiver ativa ou houver pendência/conflito. | `FirstVideoGuide`, filtros atuais, limite e ordenação local. |
| **Produção / Workflows** | `frontend/src/features/ProductionRun.tsx`, `EditorialWorkflows.tsx` | Exibir tag de estado do item no cabeçalho do card e, em caso de `CONFLITO`, exibir callout `StatusMessage state="error"` com botão `Comparar e resolver`. | Pipeline de etapas, player, timeline de palavras, revisão de licenças, aprovação humana. |
| **Biblioteca** | `frontend/src/features/Library.tsx` | Em `ImportedProjectsSection`, exibir tag de sincronização de metadados quando aplicável. Mídias brutas (`.mp4`, `.wav`, etc.) permanecem explicitamente `SOMENTE LOCAL` sem upload automático. | Player de mídia, busca (`Ctrl+F` / atalho), filtros de tipo/tamanho. |
| **Transcrição** | `frontend/src/features/Transcription.tsx` | **Nenhuma mudança na fila FIFO ou na leitura.** Preserva estado oculto ao navegar. | Fila de até 20 arquivos, pausa ao concluir, salvar antes de avançar, modelos Whisper. |
| **Histórico Operacional** | `frontend/src/features/ErrorCenter.tsx` | Incluir filtro/categoria para falhas de sincronização (`sync_auth_error`, `sync_conflict`, `sync_network_error`) usando o mesmo card `.error-center-item` e `suggested_action_pt`. | Layout de resumo KPI, chips de filtro, paginação e navegação por item. |
| **Ajustes (Geral & Privacidade)** | `frontend/src/features/Settings.tsx`, `PrivacySecurity.tsx` | Em `GERAL`: card `CONTA E SINCRONIZAÇÃO` (conectar/desconectar, sincronizar agora, modo manual/automático). Em `PRIVACIDADE E SEGURANÇA`: registrar Supabase como destino externo reconhecido sob `TRÁFEGO DE REDE` apenas quando habilitado pelo usuário, mantendo transparência de que mídia/segredos não sobem. | Seletor de tema (`SISTEMA`/`CLARO`/`ESCURO`), `CONEXÃO LOCAL`, `HERMES AGENT`, `AMBIENTE LOCAL`, `Whisper`, `Narração`, `Atalhos`. |

---

## 10. Wireframes em Texto (ASCII)

### 10.1 Shell Global (Footer + Banner de Alerta Acionável)

```text
+----------------------------------------------------------------------------------+
| HORUS / CENTRAL AUDIOVISUAL                        [APLICATIVO EM DESENVOLVIMENTO] |
+----------------------------------------------------------------------------------+
| [WARN: CONFLITO] A produção "Piloto Local #1" tem alterações locais e na nuvem.   |
|                                              [ Comparar e resolver ]  [ Fechar ] |
+----------------------------------------------------------------------------------+
|                                                                                  |
|  (Conteúdo da tela ativa sem alterações de layout)                               |
|                                                                                  |
+----------------------------------------------------------------------------------+
| (*) Backend local conectado  ·  (o) Sincronizado   | Transcrição local Whisper...|
+----------------------------------------------------------------------------------+
```
*Variações no footer:*
- Sem conta conectada: `(*) Backend local conectado · Modo local (sem nuvem)`
- Offline com pendências: `(*) Backend local conectado · (!) Offline · 2 pendentes`
- Sincronizando: `(*) Backend local conectado · (~) Sincronizando…`
- Erro de autenticação: `(*) Backend local conectado · (x) Sessão expirada (clique para entrar)`

### 10.2 Card "Conta e Sincronização" em `Ajustes → GERAL`

**Estado desconectado (padrão inicial):**
```text
+----------------------------------------------------------------------------------+
| CONTA E SINCRONIZAÇÃO                                      [ MODO LOCAL ATIVO ]  |
| Sincronize metadados entre dispositivos (opcional).                              |
|                                                                                  |
| Suas produções, roteiros e mídias funcionam 100% offline neste computador.       |
| Mídias brutas e credenciais nunca são enviadas para a nuvem.                     |
|                                                                                  |
| [ CONECTAR CONTA ]                                                               |
+----------------------------------------------------------------------------------+
```

**Estado conectado (com pendência):**
```text
+----------------------------------------------------------------------------------+
| CONTA E SINCRONIZAÇÃO                                       [ 2 PENDENTES ]      |
| Conta conectada: op***@exemplo.com                                               |
|                                                                                  |
| Última sincronização: 15/11/2026 09:30                                           |
| Modo: (x) Sincronizar quando online   ( ) Somente manual                         |
|                                                                                  |
| [ SINCRONIZAR AGORA ]   [ DESCONECTAR ]                                          |
+----------------------------------------------------------------------------------+
```

**Estado erro de autenticação (`erro_autenticacao`):**
```text
+----------------------------------------------------------------------------------+
| CONTA E SINCRONIZAÇÃO                                     [ SESSÃO EXPIRADA ]    |
| Conta conectada: op***@exemplo.com                                               |
|                                                                                  |
| ! Sua sessão na nuvem expirou. O trabalho local continua disponível, mas a       |
|   sincronização foi pausada.                                                     |
|                                                                                  |
| [ ENTRAR NOVAMENTE ]   [ CONTINUAR OFFLINE ]   [ DESCONECTAR ]                   |
+----------------------------------------------------------------------------------+
```

### 10.3 Indicadores por Item na `Central de Produção` (com sincronização ativa)

```text
| VÍDEO / PROJETO       | ETAPA          | SINCRONIZAÇÃO | PRÓXIMO PASSO           |
|-----------------------|----------------|---------------|-------------------------|
| Meu Primeiro Vídeo    | Revisão final  | [SINCRONIZADO]| Aprovar entrega         |
| Roteiro Canal Tech    | Roteiro        | [PENDENTE]    | Revisar texto (local)   |
| Piloto Episódio 02    | Planejamento   | [CONFLITO]    | [ Resolver conflito ]   |
```

### 10.4 Diálogo / Painel de Resolução de Conflito

```text
+----------------------------------------------------------------------------------+
| RESOLUÇÃO DE CONFLITO                                                [ CONFLITO ] |
| Roteiro: "Piloto Episódio 02"                                                    |
|                                                                                  |
| Este item foi alterado localmente enquanto estava offline e também possui uma    |
| revisão mais recente na nuvem. Nenhuma versão foi descartada automaticamente.    |
|                                                                                  |
| +--------------------------------------+ +-------------------------------------+ |
| | VERSÃO DESTE COMPUTADOR (LOCAL)      | | VERSÃO DA NUVEM (REMOTA)            | |
| | Atualizado em: 15/11/2026 09:12      | | Atualizado em: 15/11/2026 09:05     | |
| | Revisão: local-4 · 3 alterações      | | Revisão: cloud-4                    | |
| |                                      | |                                     | |
| | [ MANTER VERSÃO LOCAL ]              | | [ USAR VERSÃO DA NUVEM ]            | |
| +--------------------------------------+ +-------------------------------------+ |
|                                                                                  |
| [ CANCELAR E MANTER PENDENTE ]                                                   |
+----------------------------------------------------------------------------------+
```

---

## 11. Plano de Testes Playwright (Fase Futura de Implementação)

Seguindo a infraestrutura existente em `frontend/playwright.config.ts` (`channel: 'msedge'`, viewport `1320 × 840`) e os helpers determinísticos de `frontend/tests/production-journey-helpers.ts` (rotas mockadas com `page.route`, sem depender de rede externa ou projeto Supabase real):

### Suite proposta: `frontend/tests/ux-sync-states.spec.ts`

1. **Modo local padrão (sem conta conectada):**
   - Abre o app conectado ao backend local mockado.
   - Verifica que não há tela de login bloqueante; `Visão geral` e `Central de Produção` abrem normalmente.
   - Verifica no `footer` a presença de `Backend local conectado` e `Modo local` (sem tags de sync poluindo `CenterTable`).
2. **Transição dos 5 estados globais (`online`, `offline`, `sincronizando`, `conflito`, `erro_autenticacao`):**
   - Simula via mock de API cada estado e valida:
     - `sincronizando`: indicador no footer com texto `Sincronizando…` e `aria-live="polite"`.
     - `online`: indicador `Sincronizado`.
     - `offline` com pendências: indicador `Offline · 2 pendentes` e tags `[PENDENTE]` visíveis na `Central de Produção`.
     - `conflito`: banner `.production-notice` com `role="alert"` ou `role="status"` e botão `Comparar e resolver`.
     - `erro_autenticacao`: banner com mensagem em português garantindo preservação local e botão `Entrar novamente`.
3. **Continuidade offline da produção local (regressão crítica):**
   - Com estado de sincronização `offline` e `erro_autenticacao`, executa o fluxo local de criação/abertura de produção e verificação de prontidão do primeiro vídeo.
   - Confirma que nenhuma ação local é bloqueada e que o item novo/editado recebe `PENDENTE` sem erro de rede travando a tela.
4. **Fluxo de login individual (opcional) e desconexão:**
   - Navega para `Ajustes → GERAL → CONTA E SINCRONIZAÇÃO`.
   - Simula clique em `CONECTAR CONTA` (mock do retorno OAuth/sessão do backend local).
   - Valida exibição de conta mascarada, botão `SINCRONIZAR AGORA` e que `DESCONECTAR` retorna ao modo local sem remover itens locais da lista.
5. **Resolução de conflito:**
   - Clica em `Comparar e resolver` em um item com tag `CONFLITO`.
   - Valida exibição lado a lado (Versão local vs Versão da nuvem), escolhe `MANTER VERSÃO LOCAL` e confirma transição para `PENDENTE` → `SINCRONIZADO`.
6. **Acessibilidade e Contraste (Temas `light` e `dark`):**
   - Executa `AxeBuilder` (`@axe-core/playwright`, tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`) nos estados `offline`, `conflito` e `erro_autenticacao`, em ambos os temas (`light` e `dark`), garantindo zero violações de contraste, `aria-live` adequado e navegação por teclado até os botões de recuperação.
