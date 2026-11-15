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

### F.1 Linha retida do Hermes: UMA branch para integrar — `integ-hermes-line-v3` (14/11/2026)

A `integ-hermes-line-v3` está publicada em `origin`, parte da `main` de 14/11/2026 e já traz, com merges normais e conflitos resolvidos preservando a `main` (modelos Whisper locais, CUDA, `file_access`, prévia Kokoro, UX), **toda** a linha retida. **Integre somente esta**; as demais são históricas e não devem ser mescladas separadamente.

| Ordem de merge na v3 | Branch de origem | O que acrescenta | Migração | Issue |
|---|---|---|---|---|
| 1 | `yannbastos/integ-hermes-line-v2` | etapa `script` do Hermes (HOR-134), conciliação e auditoria append-only de transmissões (HOR-115) | v10 (`scripts.generator`), v11 (`transmissions`) | #121, #103 |
| 2 | `feat-hor-111-egress-gateway` | gateway único de saída HTTP (deny by default, `HORUS_EGRESS_ALLOW`, anti-rebinding) | — | #99 |
| 3 | `feat-hor-107-human-checkpoints` | checkpoints humanos para gastar e publicar, com barreira no backend | v12 (`human_checkpoints`) | #95 |
| 4 | `test-hor-130-adversarial` | bateria adversarial integrada; descrição de checkpoint sem bidi/invisíveis | — | #117 |
| 5 | `yannbastos/feat-hor-115-transmissions-ui` | tela de transmissões em Privacidade e Segurança | — | #103 |
| 6 | `yannbastos/feat-hor-107-checkpoints-ui` | histórico e diálogo de checkpoints humanos | — | #95 |

Ajustes próprios da v3 (só testes/snapshots): snapshot OpenAPI consolidado e **aditivo** (+297 linhas, 0 removidas: `HermesScriptRequest`, `CheckpointRequest`, `GET /v1/security/transmissions`, `POST /v1/workflows/{id}/scripts/reconcile`, `POST/GET /v1/checkpoints`); snapshot das respostas do primeiro vídeo com o diagnóstico `cuda_runtime` (+14, aditivo); o teste de paginação de transmissões passou a comparar a sequência de cursores (o StrictMode duplica a busca inicial).

**Validação feita na v3 (14/11/2026):** backend + `contracts/hermes`: 2490 passed, 36 skipped (as 2 falhas eram ambiente/snapshot, já tratadas: extra `transcription` ausente no venv e `cuda_runtime` fora do snapshot do primeiro vídeo); snapshot OpenAPI só aditivo; migração v9→v12 em cópia com backup verificado e idempotente; build do frontend verde; Playwright completo 236 passed, 4 skipped e 1 falha de teste corrigida (paginação de transmissões, spec reexecutado: 4 passed); jornada real com o backend do próprio worktree: teste 1 (Maria) passou em 4,8 min e a execução foi **interrompida por falta de memória do sistema** antes dos testes 2–4, que **não foram executados** nesta rodada (repetir na integração, com a máquina livre).

**Quando integrar:** somente depois de o usuário testar o primeiro vídeo na `main` atual (o fluxo não deve mudar até lá), em lote. Não use dados reais do usuário: `HORUS_DATA_DIR` temporário. O snapshot aditivo da linha está **aprovado** para a integração; qualquer diferença adicional = parar e avisar.

**Procedimento (merge normal, sem rebase nem force):**

```powershell
git fetch origin
git switch -c integ-linha-hermes-main origin/main
git merge --no-ff origin/integ-hermes-line-v3                 # se a main andou, conflitos tendem a ser só testes/snapshots
uv sync --project backend --frozen --extra test --extra transcription   # o extra transcription evita 1 falha de ambiente em test_transcription_error_paths
npm --prefix frontend ci --ignore-scripts --userconfig frontend/.npmrc
& backend/.venv/Scripts/python.exe -m pytest backend/tests contracts/hermes/tests -q      # uma vez
$env:HORUS_UPDATE_API_SNAPSHOT = '1'; & backend/.venv/Scripts/python.exe -m pytest backend/tests/test_api_contract_snapshot.py backend/tests/test_first_video_response_shapes.py -q; Remove-Item Env:HORUS_UPDATE_API_SNAPSHOT
git diff --stat -- backend/tests/snapshots                    # só linhas adicionadas
npm --prefix frontend run build
npm --prefix frontend test                                    # Playwright completo (HORUS_FRONTEND_PORT livre)
```

Depois:

1. **Migração v9→v12 em CÓPIA** de um banco de teste (nunca o real): criar o banco na v9, copiar, abrir a cópia com `horus_backend.migrations.migrate`, conferir `PRAGMA user_version = 12`, integridade, as tabelas novas (`transmissions`, `human_checkpoints`), o backup automático `horus.backup-v9-*` (`python -m horus_backend.backups verify`) e que reabrir é idempotente. Antes de migrar o banco real, gerar e verificar o pacote: `python -m horus_backend.local_data_export export <pasta-de-dados> --to <pacote.zip>` e `verify`.
2. Jornada real (`node frontend/scripts/e2e-backend.mjs run`, variáveis em [piloto-local.md](piloto-local.md)) com `HORUS_E2E_STRICT=1`; máquina carregada multiplica os tempos e a auditoria de rede do render já espera até 30 s pelo NetLog.
3. Atualizar as Issues #121, #103, #99, #95 e #117 (evidência + Status) e mover para Concluído.

Não ativar inferência real, provedores pagos nem publicação ao integrar: o gate de segurança e os checkpoints humanos continuam bloqueando por padrão. A decisão pendente de provedor/modelo está em [avaliacao-provedores-hermes.md](avaliacao-provedores-hermes.md).

Siga a seção "Eficiência de consumo" de [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md).
