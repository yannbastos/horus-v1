# Retomada do Maestro

Procedimento único para iniciar, retomar, sincronizar ou transferir a coordenação do Hórus em uma sessão nova, em qualquer computador. É usado pelas skills `horus-maestro` (`.claude/skills/` para Claude Code e `.agents/skills/` para Codex e Antigravity) e pode ser seguido manualmente. Não depende do histórico de conversas: o estado oficial está no Git e no GitHub Projects.

**Descoberta da skill:** o Claude Code só encontra `.claude/skills/` da pasta em que a sessão foi aberta. Abra a sessão na raiz do repositório `horus`. Se ela for aberta na pasta de trabalho que contém vários repositórios (ou em um nível acima, como a pasta configurada no Orca), a skill não aparece: invoque-a depois de abrir na raiz certa, gere uma ponte local em `<pasta de trabalho>/.claude/skills/horus-maestro/` com `scripts/install-workspace-skill-bridge.ps1` (passando `-WorkspacePath <pasta>` quando a sessão abrir acima da pasta-mãe imediata; sem regras próprias), ou siga este documento manualmente.

Invocação explícita: abra a sessão na raiz e use `/horus-maestro` quando disponível, ou siga este procedimento manualmente; a presença da skill não promete carregamento automático. Se precisar instalar a ponte, faça-o antes da retomada somente leitura, a partir da pasta que contém o repositório:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File horus/scripts/install-workspace-skill-bridge.ps1
```

Em uma pasta ancestral acima da pasta-mãe imediata, informe o workspace:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HORUS/horus/scripts/install-workspace-skill-bridge.ps1 -WorkspacePath .
```

Todos os caminhos são relativos à raiz do repositório. Os comandos `git` e `gh` funcionam em PowerShell e em shells POSIX.

## Regras durante a inicialização

As etapas A a D são **somente leitura**:

- Nada de `reset`, `clean`, `checkout` de arquivos, `merge`, `pull`, `rebase`, `stash` ou `push`. `git fetch` é permitido: só atualiza referências remotas.
- Não instalar dependências, não iniciar o gateway Hermes, não alterar perfis, credenciais, configurações, Issues ou o Project.
- Não imprimir tokens. Use `gh auth status` sem `--show-token`.

Só depois do resumo da etapa E o Maestro volta a operar, dentro do que a [governança dos agentes](AGENT-GOVERNANCE.md) autoriza.

## A. Contexto

Leia progressivamente, nesta ordem, buscando os cabeçalhos antes de abrir trechos:

1. Identifique o papel: todos leem [AGENTS.md](../AGENTS.md) e as seções obrigatórias da [governança](AGENT-GOVERNANCE.md) indicadas nele, buscando por cabeçalho. O Maestro lê a governança inteira e [CLAUDE.md](../CLAUDE.md) quando usar Claude; worker segue apenas o escopo atribuído.
   Após identificar o papel e antes de coordenar, o Maestro lê [MAESTRO-OPERATIONS.md](MAESTRO-OPERATIONS.md) uma vez; não reler se inalterado. Worker não precisa desse documento.
2. [ROADMAP.md](ROADMAP.md): somente a seção de fase/estado pertinente. Localize-a com `rg -n '^## ' docs/ROADMAP.md`; o estado operacional vigente é confirmado no Project/Issues na etapa C, não nos checkpoints datados do ROADMAP.
3. [ARCHITECTURE.md](ARCHITECTURE.md#implementação-atual): somente "Implementação atual".
4. [CONTINUAR-EM-OUTRO-PC.md](CONTINUAR-EM-OUTRO-PC.md): somente em máquina nova.
5. Documentos da fase atual: **sob demanda**, quando a tarefa tocar o tema, lendo somente a seção pertinente. Para piloto, mídia ou qualidade, por exemplo: [piloto-local.md](piloto-local.md), [contratos-midia.md](contratos-midia.md) e [qualidade-piloto.md](qualidade-piloto.md). A presença de um link no ROADMAP não exige leitura integral nem recursiva.

Preferências de modelos, cotas e coordenação: [MAESTRO-OPERATIONS.md](MAESTRO-OPERATIONS.md); a governança prevalece.

## B. Git

```text
git rev-parse --show-toplevel
git remote get-url origin
git fetch origin
git status -sb
git rev-parse --short HEAD
git rev-parse --short origin/main
git log --oneline -5 origin/main
git log --oneline origin/main..HEAD
git branch -a --no-merged origin/main
git worktree list
```

Confirme:

- `origin` aponta para `yannbastos/horus`.
- Em que branch a sessão está, e qual é o HEAD.
- Se há commits locais não enviados (`origin/main..HEAD`).
- Branches publicadas ou locais ainda não integradas: são entregas pendentes.
- Worktrees ativos. Em cada worktree com trabalho pendente, rode `git -C <worktree> status --short` e preserve alterações não commitadas.

## C. Tarefas

```text
gh auth status
gh project item-list 1 --owner yannbastos --limit 300 --format json --jq '.items[] | select(.status == "Em andamento" or .status == "Em revisão" or .status == "Bloqueado") | {titulo: .title, status: .status, Agente: .agente, Prioridade: .prioridade, issue: .content.url}'
# Consulte as Issues selecionadas acima, sem listar todas as abertas:
gh issue view <número> -R yannbastos/horus-management --comments
gh pr list -R yannbastos/horus
```

- O comando do Project filtra na saída do próprio `gh` somente **Em andamento**, **Em revisão** e **Bloqueado**, com título, Agente, Prioridade e URL da Issue. Consulte apenas essas Issues; sem listagem ampla de abertas. Se houver mais itens que o limite, pagine antes de concluir o inventário.
- Para cada um, o último comentário da Issue é o ponto de retomada: branch, commits, evidências e pendências.
- Antes de criar qualquer Issue, procure o ID `HOR-XXX` em Issues abertas e fechadas, para não duplicar.
- Se o `gh` não estiver autenticado ou não tiver o escopo `project`, registre só o bloqueio e o procedimento: `gh auth login`, ou `gh auth refresh -s project`. Siga com o que não depende do GitHub.

## D. Ambiente

Só verificação, sem instalar nem configurar:

```text
orca --version
orca worktree ps --json
orca orchestration run-list --json
codex --version
claude --version
agy --version
uv --version
node --version
npm --version
```

- Ambientes de teste: `backend/.venv` e `frontend/node_modules`. Se faltarem, anote. A preparação pelos lockfiles (`uv sync --project backend --frozen --extra test`, `npm --prefix frontend ci`) vem depois do resumo, como operação autorizada pela governança.
- Runs, mensagens e worktrees do Orca são locais. Em máquina nova, eles não existem; isso não é perda de estado.
- Hermes: não iniciar o gateway, não alterar o perfil. Se o backend já estiver rodando, consulte o estado de segurança pela tela **Ajustes → Privacidade e Segurança** ou pelo endpoint autenticado de status. Caso contrário, registre "Hermes não verificado nesta sessão". A inferência real continua desabilitada.
- Limites de uso dos agentes: um agente sem cota é registrado como indisponível, e o trabalho é redistribuído.

## E. Resumo e retomada

Apresente ao usuário, em poucas linhas:

- Estado atual do produto e fase.
- Últimas entregas integradas (`origin/main`).
- Tarefas em andamento, com agente e branch.
- Bloqueios reais e o que eles exigem do usuário.
- Próximas tarefas independentes e o agente indicado para cada uma.

Depois disso, retome a coordenação conforme a governança:

- Revise e integre entregas prontas.
- Recrie worktrees a partir das branches publicadas.
- Delegue tarefas independentes.
- Atualize Issue, Status do Project e evidências.
- Encerre tarefas concluídas conforme [Encerramento de tarefas e limpeza](AGENT-GOVERNANCE.md#encerramento-de-tarefas-e-limpeza).

Continua exigindo autorização do usuário: inferência real, APIs pagas, credenciais, publicação, envio de dados privados e Git destrutivo.

### Handoff de Maestro no Orca

Para transferir o Maestro ativo a outra sessão local, mantenha as duas abas abertas e use o mesmo worktree:

1. Pause a coordenação na sessão antiga. Registre o ID do Run e o handle do terminal (`$env:ORCA_TERMINAL_HANDLE`), Issue, branch/HEAD, escopo, workers/dispatches ativos e perguntas pendentes. Entregue essas referências à sessão nova sem copiar transcrições ou segredos.
2. Na sessão nova, confira a raiz Git e execute `& ./scripts/horus-handoff.ps1 -PreviousTerminal <handle> -RunId <id>`. Esse comando transfere o vínculo Orca por `run-use` e confirma o novo proprietário. Preserve os dispatches existentes.
3. Na sessão nova, confirme `worker-list --run <id>` e leia a caixa `inbox --terminal run:<id> --full`. Confirme à sessão antiga o Run, os dispatches e a prontidão. Só então encerre a aba antiga, se desejado. Se uma verificação falhar, mantenha ambas abertas e confira `run-current` de cada lado antes de qualquer nova tentativa.

## F. Branches retidas (não estão no Project como código)

Branches retidas por decisão não são branches temporárias concluídas: preserve-as conforme a governança. A integração de `integ-hermes-line-v3` e a Fase E do Supabase exigem autorização específica do usuário.

Consulte a Issue correspondente e o [Project](https://github.com/users/yannbastos/projects/1) para o estado vigente. Composição e checkpoints datados estão no [histórico de novembro de 2026](historico/retomada-checkpoints-2026-11.md); leia-o somente para investigar uma pendência concreta, sem tratar planos antigos como autorização.
