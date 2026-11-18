# Retomada do Maestro

Procedimento único para iniciar, retomar, sincronizar ou transferir a coordenação do Hórus em uma sessão nova, em qualquer computador. É usado pelas skills `horus-maestro` (`.claude/skills/` para Claude Code e `.agents/skills/` para Codex e Antigravity) e pode ser seguido manualmente. Não depende do histórico de conversas: o estado oficial está no Git e no GitHub Projects.

Abra a sessão na raiz do checkout e invoque explicitamente `/horus-maestro`. Para sessões Claude Code em pasta ancestral, instale as pontes com o [bootstrap](CONTINUAR-EM-OUTRO-PC.md#15-retomar-a-coordenação-dos-agentes) antes de iniciar a retomada. Se a skill estiver indisponível, siga este procedimento manualmente.

Os caminhos são relativos à raiz do repositório. Os comandos `git` e `gh` funcionam em PowerShell e em shells POSIX.

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
- Para cada item, recupere o último [checkpoint](#checkpoints-nas-issues), compare com o estado atual do Project e confira comentários humanos posteriores. Confira o autor do comentário e sua autoridade sobre a frente. Cruze responsável, branch, SHAs publicados e PR com Git, worktrees e Runs locais. Cruze também Issues e PRs relacionados por escopo e arquivos.
- Um checkpoint ausente, inválido, incompatível com o estado atual, ou com execução `em execução`/`incerta` sem confirmação local exige reconciliação. Confira o responsável anterior, a existência de alterações locais/publicadas e se outro Maestro ou worker ainda atua. Ausência de Run local, inatividade no PR e idade do checkpoint são insuficientes para liberar o escopo. Confirme a liberação com o responsável (ou decisão explícita do usuário após verificar o risco de perda e duplicação) e registre-a na Issue antes de redistribuir. Outras frentes independentes podem avançar.
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
- Para escopos liberados, reutilize o worktree local correspondente ou recrie-o a partir da branch publicada e confirme o HEAD. Se a branch ainda não existia e o trabalho puder começar, parta da `origin/main` verificada. Preserve qualquer divergência local e reconcilie antes de mexer nela.
- No terminal dedicado do Maestro, use `orca orchestration run-current --full --json`. Se já houver um Run próprio, reutilize-o. Se não houver vínculo nem outro Maestro local coordenando o mesmo escopo, crie um com `orca orchestration run-create --objective "Hórus Maestro — <escopo>" --full --json`. Confirme o vínculo com `run-current` antes de delegar, com ou sem Supervisor. Se a criação der resposta incerta, inspecione o vínculo e use o recibo de retry quando disponível antes de tentar novamente.
- Delegue tarefas independentes que passaram pela reconciliação, seguindo [Operações do Maestro](MAESTRO-OPERATIONS.md#delegação).
- Atualize Issue, Status do Project e evidências.
- Encerre tarefas concluídas conforme [Encerramento de tarefas e limpeza](AGENT-GOVERNANCE.md#encerramento-de-tarefas-e-limpeza).

Continua exigindo autorização do usuário: inferência real, APIs pagas, credenciais, publicação, envio de dados privados e Git destrutivo.

### Checkpoints nas Issues

Em início, entrega para revisão, bloqueio ou decisão que altere a retomada, handoff e encerramento, comente na Issue existente. Consolide operações intermediárias. O Status vigente continua no Project; o checkpoint registra o estado observado naquele momento. Confirme o Status após cada transição. O campo `execution` descreve o escopo no momento do registro: `livre` quando foi explicitamente liberado, `em execução` quando há responsável executando, ou `incerta` quando falta confirmação.

Salve temporariamente em UTF-8, preencha as listas com evidências resumidas (`[]` se vazias), use SHAs completos e datas UTC e use `null` para branch/PR ainda inexistentes. Registre decisões humanas com data, autor, escopo e referência verificável. Revise a visibilidade das evidências e mantenha fora do comentário credenciais, IDs de Runs/terminais, dados privados, conversas e bancos locais.

````markdown
[HORUS-CHECKPOINT v1]

~~~json
{
  "task": "HOR-999",
  "objective": "Objetivo aprovado na Issue",
  "owner": "Responsável pela próxima ação",
  "status": "Em andamento",
  "timestamp": "2026-11-18T15:30:00Z",
  "branch": null,
  "commits": [],
  "pr": null,
  "done": [],
  "evidence": [],
  "tests": [],
  "pending": ["Verificar o escopo"],
  "dependencies": [],
  "blockers": [],
  "decisions": [],
  "next": "Conferir critérios e dependências na Issue",
  "execution": "incerta"
}
~~~
````

```powershell
# $arquivo é o caminho do arquivo temporário. $numero é o número GitHub da Issue.
& ./scripts/horus-checkpoint.ps1 -Path $arquivo
# Após validação e revisão manual do conteúdo e destinatário:
gh issue comment $numero -R yannbastos/horus-management --body-file $arquivo
& ./scripts/horus-checkpoint.ps1 -Issue $numero
```

A leitura percorre os comentários e valida o mais recente com marcador reservado, incluindo a correspondência do ID com o título da Issue. Se falhar, confira a Issue e registre um checkpoint corretivo após reconstruir o estado. Use a opção `-RequireReleased` para exigir uma liberação registrada antes de redistribuir, além de conferir evidências e atividade conforme a etapa C. A validação de formato detecta padrões comuns de segredos; a revisão do conteúdo continua obrigatória.

### Supervisor e handoff no Orca

Abra um terminal Claude Code dedicado e invoque `/supervisar` quando quiser centralizar vários Maestros. A descoberta funciona na raiz do Git e nas pontes de workspace instaladas pelo bootstrap. Cada worktree precisa conter a versão com a skill. Maestros consultam o Supervisor na retomada conforme [Operações do Maestro](MAESTRO-OPERATIONS.md#supervisão-opcional).

Para transferir um Maestro ou Supervisor ativo a outra sessão local, mantenha as duas abas abertas e use o mesmo worktree:

1. Pause a coordenação na sessão antiga. Registre o ID do Run e o handle do terminal (`$env:ORCA_TERMINAL_HANDLE`), Issue, branch/HEAD, escopo, workers/dispatches ativos e perguntas pendentes. Entregue essas referências à sessão nova sem copiar transcrições ou segredos.
2. Na sessão nova, confira a raiz Git e execute `& ./scripts/horus-supervisor.ps1 Handoff -PreviousTerminal <handle> -RunId <id>`. Esse comando transfere o vínculo Orca por `run-use` e confirma o novo proprietário. Preserve os dispatches existentes.
3. Na sessão nova, confirme `worker-list --run <id>` e leia a caixa `inbox --terminal run:<id> --full`. Confirme à sessão antiga o Run, os dispatches e a prontidão. Só então encerre a aba antiga, se desejado. Se uma verificação falhar, mantenha ambas abertas e confira `run-current` de cada lado antes de qualquer nova tentativa.

Registre o checkpoint do handoff sem os identificadores locais. O Supervisor usa a mesma caixa postal após o handoff. Se o Supervisor sair, os Maestros continuam seu fluxo normal. A nova ativação na mesma cópia local do Git recupera a caixa anterior quando o proprietário já não estiver conectado.

## F. Branches retidas (não estão no Project como código)

Branches retidas por decisão não são branches temporárias concluídas: preserve-as conforme a governança. A integração de `integ-hermes-line-v3` e a Fase E do Supabase exigem autorização específica do usuário.

Consulte a Issue correspondente e o [Project](https://github.com/users/yannbastos/projects/1) para o estado vigente. Composição e checkpoints datados estão no [histórico de novembro de 2026](historico/retomada-checkpoints-2026-11.md); leia-o somente para investigar uma pendência concreta, sem tratar planos antigos como autorização.
