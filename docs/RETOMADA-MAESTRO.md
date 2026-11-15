# Retomada do Maestro

Procedimento único para iniciar, retomar, sincronizar ou transferir a coordenação do Hórus em uma sessão nova, em qualquer computador. É usado pelas skills `horus-maestro` (`.claude/skills/` para Claude Code e `.agents/skills/` para Codex) e pode ser seguido manualmente. Não depende do histórico de conversas: o estado oficial está no Git e no GitHub Projects.

Todos os caminhos são relativos à raiz do repositório. Os comandos `git` e `gh` funcionam em PowerShell e em shells POSIX.

## Regras durante a inicialização

As etapas A a D são **somente leitura**:

- Nada de `reset`, `clean`, `checkout` de arquivos, `merge`, `pull`, `rebase`, `stash` ou `push`. `git fetch` é permitido: só atualiza referências remotas.
- Não instalar dependências, não iniciar o gateway Hermes, não alterar perfis, credenciais, configurações, Issues ou o Project.
- Não imprimir tokens. Use `gh auth status` sem `--show-token`.

Só depois do resumo da etapa E o Maestro volta a operar, dentro do que a [governança dos agentes](AGENT-GOVERNANCE.md) autoriza.

## A. Contexto

Leia, nesta ordem:

1. [AGENTS.md](../AGENTS.md) e [CLAUDE.md](../CLAUDE.md).
2. [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md): papéis, autonomia e restrições.
3. [ROADMAP.md](ROADMAP.md): síntese do estado e da fase atual.
4. [ARCHITECTURE.md](ARCHITECTURE.md), seção "Implementação atual".
5. [CONTINUAR-EM-OUTRO-PC.md](CONTINUAR-EM-OUTRO-PC.md), se a máquina for nova.
6. Os documentos da fase atual citados no ROADMAP. Na fase do piloto audiovisual: [piloto-local.md](piloto-local.md), [contratos-midia.md](contratos-midia.md) e [qualidade-piloto.md](qualidade-piloto.md).

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
gh project item-list 1 --owner yannbastos --limit 300 --format json
gh issue list -R yannbastos/horus-management --state open --limit 300
gh issue view <número> -R yannbastos/horus-management --comments
gh pr list -R yannbastos/horus
```

- No Project, filtre os itens **Em andamento**, **Em revisão** e **Bloqueado**, com Agente e Prioridade.
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

Continua exigindo autorização do usuário: inferência real, APIs pagas, credenciais, publicação, envio de dados privados e Git destrutivo.

## F. Branches retidas (não estão no Project como código)

Algumas entregas validadas ficam fora da `main` por decisão de integração (ex.: migrações enquanto o usuário testa). Antes de delegar, confira na Issue indicada:

### F.1 Cadeia retida da linha Hermes (estado de 14/11/2026)

Todas as branches estão publicadas em `origin` e **nenhuma está na `main`**. Cada uma contém a anterior; migrações aditivas v10–v12.

| Ordem | Branch | O que acrescenta | Migração | Issue de gestão |
|---|---|---|---|---|
| 1 | `yannbastos/integ-hermes-line-v2` | etapa `script` do Hermes (HOR-134) + conciliação + auditoria append-only de transmissões (HOR-115), já integradas entre si | v10 (`scripts.generator`), v11 (`transmissions`) | #121, #103 |
| 2 | `feat-hor-111-egress-gateway` | gateway único de saída HTTP, deny by default, allowlist (`HORUS_EGRESS_ALLOW`), anti-rebinding, auditoria; allowlist de download derivada de `is_youtube_url` | — | #99 |
| 3 | `feat-hor-107-human-checkpoints` | checkpoints humanos para gastar e publicar, com barreira no backend | v12 (`human_checkpoints`) | #95 |
| paralela | `yannbastos/feat-hor-115-transmissions-ui` | tela de transmissões (frontend); parte de `integ-hermes-line-v2`, **não** depende de 2 e 3 | — | #103 |

As branches originais `feat-hor-134-script-stage`, `fix-hor-134-script-stage-review` e `feat-hor-115-transmission-audit` já estão contidas na linha (não integre separadamente). A `integ-hermes-line` (v1) é anterior e foi substituída pela v2.

**Contrato da API:** todo o aumento é aditivo e o snapshot (`backend/tests/snapshots/openapi.json`) já está atualizado nas branches. **Aprovado** para a integração desta linha após o teste do primeiro vídeo: `HermesScriptRequest`, `GET /v1/security/transmissions`, `POST /v1/workflows/{id}/scripts/reconcile` (ordem 1) e `POST/GET /v1/checkpoints` (ordem 3). Diferença extra no snapshot ao integrar = parar e avisar.

**Quando integrar:** somente depois de o usuário testar o primeiro vídeo na `main` atual (o fluxo não deve mudar até lá), em lote, com a suíte completa uma única vez. Não use dados reais do usuário: `HORUS_DATA_DIR` temporário.

**Procedimento e validação (a partir de `origin/main` atualizada; merge normal, sem rebase nem force):**

```powershell
git fetch origin
git switch -c integ-linha-hermes origin/main
git merge --no-ff origin/yannbastos/integ-hermes-line-v2      # 1; conflitos esperados em api.py (imports/handlers): manter o que a main já tem
git merge --no-ff origin/feat-hor-111-egress-gateway          # 2
git merge --no-ff origin/feat-hor-107-human-checkpoints       # 3
uv sync --project backend --frozen --extra test
& backend/.venv/Scripts/python.exe -m pytest backend/tests contracts/hermes/tests -q      # uma vez
$env:HORUS_UPDATE_API_SNAPSHOT = '1'; & backend/.venv/Scripts/python.exe -m pytest backend/tests/test_api_contract_snapshot.py -q; Remove-Item Env:HORUS_UPDATE_API_SNAPSHOT
git diff --stat -- backend/tests/snapshots                    # só linhas adicionadas, nada removido
```

Conflitos prováveis ao integrar na `main` de hoje: `backend/src/horus_backend/api.py` (imports e handlers de `script_call`), testes que fixam `user_version` (já usam `SCHEMA_VERSION` nas branches) e a allowlist de `test_network_inventory.py` (o gateway acrescenta F-20). Depois da suíte:

1. **Migração v9→v12 em CÓPIA** de um banco de teste (nunca o real): criar o banco na v9, copiar, abrir a cópia com `horus_backend.migrations.migrate`, conferir `PRAGMA user_version = 12`, integridade, o backup automático `horus.backup-v9-*` (verificável com `python -m horus_backend.backups verify`) e que reabrir é idempotente. Antes de migrar o banco real do usuário, gerar o pacote verificável: `python -m horus_backend.local_data_export export <pasta-de-dados> --to <pacote.zip>` e `verify`.
2. Frontend da ordem paralela (`feat-hor-115-transmissions-ui`): `npm --prefix frontend ci --ignore-scripts`, `npm --prefix frontend run build`, `npm --prefix frontend test` (Playwright). Integrar por último, com a identidade visual preservada.
3. Jornada real opcional (`node frontend/scripts/e2e-backend.mjs run`, variáveis em [piloto-local.md](piloto-local.md)); com a máquina carregada os tempos sobem ~2,5× e a auditoria de rede do render já espera até 30 s pelo NetLog.
4. Atualizar as Issues #121, #103, #99 e #95 (evidência + Status) e mover para Concluído.

Não ativar inferência real, provedores pagos nem publicação ao integrar: o gate de segurança e os checkpoints humanos continuam bloqueando por padrão.

Siga a seção "Eficiência de consumo" de [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md).

## G. Ponto de pausa (14/11/2026, noite)

Pausa pedida pelo usuário. `main` publicada e validada (backend completo 2124 passed em e5d7c17; jornada real 3/3 sobre a produção no app instalado; Playwright completo 234/235 com a única falha corrigida em seguida). Nenhum agente em execução.

- Aguardando o usuário: teste humano do primeiro vídeo (guia completo, inclusive prévia Kokoro); autorização específica para integrar `integ-hermes-line-v3` (§F.1); decisão sobre download automático do modelo Whisper; exclusão de dados locais (desenho em docs/seguranca-dados.md).
- Retomar: revisão independente de docs/avaliacao-provedores-hermes.md (Issue #104, branch `review-hor-116-providers` se existir); variante Final da jornada (HORUS_E2E_FINAL=1) a repetir na `main` atual; UI das notificações e da importação já estão na `main`.
- Regras de despacho: um Codex por vez (pedido do usuário); Antigravity sempre em worker novo com `--model gemini-4-hydrogen-medium`; confirmar `activity=working` após cada despacho.
