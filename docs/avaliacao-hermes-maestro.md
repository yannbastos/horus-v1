# Avaliação: Hermes Agent como futuro Maestro (HOR-136)

Data da avaliação: 2026-11-15  
Responsável pela pesquisa e PoC: Antigravity (Gemini 4 Hydrogen)  
Issue de referência: [yannbastos/horus-management#123](https://github.com/yannbastos/horus-management/issues/123)  
Escopo: **somente avaliação; nenhuma migração, alteração no Maestro atual, alteração no Hermes do produto ou gasto foi executado.**

## Legenda de evidências

- **[TESTADO: ...]** — comando, leitura de código instalado ou inspeção executada localmente nesta sessão (evidências brutas em `C:\00. Projetos\01. HORUS\relatorios\RODADA-2026-11-15\hor-136\`).
- **[FONTE: URL (acesso em 2026-11-15)]** — documentação oficial ou repositório público consultado em 2026-11-15.
- **[HIPÓTESE]** / **[ESTIMATIVA]** — inferência técnica ou cálculo comparativo não medido em execução paga.

---

## 1. Viabilidade

### 1.1 Viabilidade técnica de coordenação

- **Orca CLI é agnóstico ao chamador:** a orquestração do Orca é exposta via CLI (`orca orchestration run-create`, `task-create`, `worker-start`, `worker-show`, `worker-read`, `send`, `reply`, `check`) [TESTADO: `orca orchestration --help`, `orca skills get orchestration`]. Como o Hermes Agent possui ferramenta de terminal (`terminal`), ele pode, em tese, executar comandos `orca orchestration` para criar Runs, tasks e iniciar workers (`codex`, `claude`, `antigravity`).
- **Lacuna crítica de integração nativa com o Orca:** o comando `orca orchestration worker-start --agent <id>` aceita apenas agentes integrados ao Orca (`claude`, `codex`, `cursor`, `antigravity`, `muse`, `zcode`, `opencode`, `opencode2`) [TESTADO: `orca orchestration worker-start --help`]. O Hermes **não** possui adaptador nativo no Orca. Enquanto o Claude Code recebe wake-up reativo automático do Orca quando um worker termina ou envia `ask`, o Hermes operaria fora desse ciclo reativo, exigindo espera manual do usuário ou polling explícito (prática explicitamente proibida pela skill `orchestration` do Orca por consumir mensagens da fila de wake-up) [TESTADO: `orca skills get orchestration`].

### 1.2 Viabilidade de autenticação e custo (fator bloqueante)

- **Assinatura Claude Code (Pro/Max) não pode ser usada no Hermes:** os Termos de Uso e a documentação oficial da Anthropic restringem tokens OAuth de assinaturas de consumo (Pro/Max) aos produtos oficiais da Anthropic (`Claude.ai` e binário oficial não modificado do `Claude Code`), proibindo seu uso em agentes ou wrappers de terceiros [FONTE: https://support.anthropic.com (acesso em 2026-11-15); https://claude.com (acesso em 2026-11-15)].
- **O código instalado do Hermes NÃO bloqueia o uso da assinatura; o impedimento é contratual, não técnico (corrigido na revisão de segurança HOR-136):** na versão instalada (`Hermes Agent v0.23.1`, commit `3b4215785882`), `agent/anthropic_credentials.py` implementa um fluxo OAuth PKCE próprio (`run_hermes_oauth_login_pure`, texto «Authorize Hermes with your Claude Pro/Max subscription») e resolve `ANTHROPIC_TOKEN`/`CLAUDE_CODE_OAUTH_TOKEN` e, como fallback emprestado, `~/.claude/.credentials.json` (`resolve_anthropic_token`, linhas ~3–5, 706 e 735). Em `hermes_cli/auth.py` (linhas 203–209 e 1256–1346) `CLAUDE_CODE_OAUTH_TOKEN` e o arquivo do Claude Code só ficam de fora da checagem de **configuração explícita** (`_IMPLICIT_ENV_VARS`, `is_provider_explicitly_configured`, para não aparecerem como provedor configurado pelo usuário); o token continua na lista de descoberta e o adaptador o roteia pelo caminho OAuth pelo prefixo [TESTADO: leitura somente leitura de `%LOCALAPPDATA%/hermes/hermes-agent`]. Ou seja, tecnicamente o Hermes consegue usar a assinatura Pro/Max; a restrição vem dos Termos da Anthropic acima, e ela é aplicada pela Anthropic (bloqueio de conta/token), não pelo Hermes.
- **Conclusão de viabilidade:** tecnicamente possível via CLI do Orca com limitações de wake-up, mas **economicamente desvantajoso** hoje porque substitui um Maestro já coberto pela assinatura do Claude Code por um Maestro que, para respeitar os Termos da Anthropic, exige **faturamento variável por API paga**.

---

## 2. Resultado da PoC isolada e limitações observadas

A PoC foi executada em Windows com `HERMES_HOME` apontando para pasta temporária exclusiva (`%TEMP%\horus-poc-hermes-136`), sem tocar no perfil `horus` do produto, sem alterar o Maestro atual, sem configurar chaves pagas e sem despachar workers no Orca.

### 2.1 O que foi testado e verificado

1. **Inventário de CLIs instalados** [TESTADO: `versions.txt`]:
   - `orca`: `1.4.259`
   - `hermes`: `Hermes Agent v0.23.1 (2026.9.24) · stable · local 3b4215785882` (Python `3.14.9`, OpenAI SDK `2.54.0`)
   - `claude`: `2.1.325 (Claude Code)`
   - `codex`: `codex-cli 0.169.0`
   - `agy`: `1.5.9`
2. **Contrato de orquestração do Orca (somente leitura)** [TESTADO: `orca-help.txt`, `orca-orchestration-help.txt`, `orca-run-list.txt`]:
   - Confirmados os verbos `run-create`, `task-create`, `worker-start`, `worker-show`, `worker-read`, `send`, `reply` e `ask`, bem como a lista de agentes aceitos em `--agent`.
3. **Subcomandos de ajuda do Hermes** [TESTADO: `poc-hermes-cli-version.txt`, `poc-hermes-config-help.txt`, `poc-hermes-backup-help.txt`, `poc-hermes-model-help.txt`, `poc-hermes-profile-help.txt`, `poc-hermes-skills-help.txt`]:
   - `--version` e `--help` dos subcomandos (`config`, `backup`, `model`, `profile`, `skills`) responderam localmente sem erros e confirmaram a estrutura de perfis (`hermes profile export/import/install`), backups (`hermes backup`) e skills (`hermes skills trust/list/install`).
4. **Limitação observada no isolamento por `HERMES_HOME` no Windows** [TESTADO: `poc-hermes-status.txt`, `poc-chat-status.txt`, `poc-chat-err.txt`]:
   - Ao executar subcomandos operacionais (`hermes status`, `hermes skills list`, `hermes chat -q "test" --safe-mode`) com um `HERMES_HOME` novo e vazio, o Hermes v0.23.1 disparou automaticamente o bootstrap de dependências para o novo data root (`hermes: preparing dependencies for this data root (the checkout's updates belong to C:\Users\yannf\AppData\Local\hermes)...`), excedendo o limite de 15 s no ensaio sem rede/instalação completa.
   - Sem `ANTHROPIC_API_KEY` configurada (proibida pelas regras de gasto zero da PoC), nenhuma inferência real foi executada.
5. **Limpeza do ambiente temporário** [TESTADO]:
   - Ao final dos ensaios, o diretório `%TEMP%\horus-poc-hermes-136` foi removido integralmente com `Remove-Item -Recurse -Force` (ver Seção 8).

---

## 3. Comparação das 4 configurações

| Dimensão | Claude Code + Opus (atual) | Claude Code + Sonnet | Hermes + Sonnet | Hermes + Opus |
|---|---|---|---|---|
| **Delegação** | Nativa no Orca (`orchestration` skill + `orca orchestration worker-start`) [TESTADO: `orca skills get orchestration`]. | Idêntica ao Claude Code + Opus; mesmo harness, apenas modelo menor [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | Via ferramenta `terminal` chamando `orca orchestration`, sem `--agent hermes` nativo no Orca [TESTADO: `orca orchestration worker-start --help`]. | Idêntica ao Hermes + Sonnet [TESTADO: `orca orchestration worker-start --help`]. |
| **Coordenação (wake-up)** | Reativa nativa: Orca acorda o coordenador automaticamente quando workers respondem [TESTADO: `orca skills get orchestration`]. | Reativa nativa idêntica [TESTADO: `orca skills get orchestration`]. | Sem wake-up nativo do Orca; exige espera manual do usuário ou risco de polling [HIPÓTESE]. | Idêntica ao Hermes + Sonnet [HIPÓTESE]. |
| **Memória** | `CLAUDE.md` + auto-memory em `~/.claude/projects/.../memory/` (índice carregado no início) [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | Idêntica ao Claude Code + Opus [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | `MEMORY.md` (padrão 2.200 chars) + `USER.md` (1.375 chars) injetados no prompt + skills progressivas [TESTADO: `agent/*.py`]. | Idêntica ao Hermes + Sonnet [TESTADO: `agent/*.py`]. |
| **Tokens (eficiência)** | Alto consumo relativo se Opus ler diffs/logs; mitigado por prompt caching e `AGENT-GOVERNANCE.md` [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | Mesmo volume de tokens do Opus, com menor uso de reasoning em tarefas simples [HIPÓTESE]. | Economia modesta no prompt por `MEMORY.md` limitado e `SKILL.md` sob demanda; também usa prompt caching [TESTADO: `agent/agent_runtime_helpers.py`]. | Mesmo volume de tokens do Hermes + Sonnet, porém em modelo 2× mais caro [FONTE: https://claude.com (acesso em 2026-11-15)]. |
| **Custos** | Coberto pela assinatura Claude Pro ($20/mês), Max ($100/mês) ou Ultra ($200/mês) [FONTE: https://claude.com (acesso em 2026-11-15)]. | Coberto pela mesma assinatura Claude Pro/Max/Ultra, consumindo menos cota semanal que Opus [FONTE: https://claude.com (acesso em 2026-11-15)]. | **API paga necessária para uso em conformidade com os Termos** (Sonnet 5.5: $2/MTok entrada, $10/MTok saída, $0,10/MTok cache read) [FONTE: https://claude.com (acesso em 2026-11-15)]. | **API paga necessária para uso em conformidade com os Termos** (Opus 5.5: $4/MTok entrada, $20/MTok saída, $0,20/MTok cache read) [FONTE: https://claude.com (acesso em 2026-11-15)]. |
| **Segurança** | Escopo por projeto, aprovações no CLI e sem daemon/gateway persistente próprio [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | Idêntica ao Claude Code + Opus [FONTE: https://code.claude.com (acesso em 2026-11-15)]. | Superfície maior: gateway, cron, plugins, MCP e hooks podem reativar capacidades se não endurecidos [TESTADO: `docs/seguranca-hermes.md`]. | Idêntica ao Hermes + Sonnet [TESTADO: `docs/seguranca-hermes.md`]. |
| **Portabilidade (entre PCs)** | Alta via Git + GitHub Projects + `docs/RETOMADA-MAESTRO.md` + `.claude/skills/horus-maestro/` [TESTADO: `docs/RETOMADA-MAESTRO.md`]. | Idêntica ao Claude Code + Opus [TESTADO: `docs/RETOMADA-MAESTRO.md`]. | Requer instalar/atualizar Hermes, preparar venv por `HERMES_HOME` e manter perfil separado do perfil `horus` [TESTADO: PoC]. | Idêntica ao Hermes + Sonnet [TESTADO: PoC]. |
| **Manutenção** | Baixa: apenas `claude` atualizado e docs no Git [TESTADO: `docs/CONTINUAR-EM-OUTRO-PC.md`]. | Baixa: idêntica ao Claude Code + Opus. | Média/Alta: atualizar Hermes, auditar plugins/skills, isolar perfil Maestro do perfil `horus` do produto [TESTADO: `docs/seguranca-hermes.md`]. | Média/Alta: idêntica ao Hermes + Sonnet. |

---

## 4. Estimativa de economia de tokens e custos (método explícito)

**[ESTIMATIVA — sem execução paga na PoC]**

### 4.1 Método de cálculo

Considerando uma sessão típica de coordenação do Maestro (20 turnos: leitura de contexto inicial, despacho de 3 workers, revisão de `git diff --stat`, atualização de Issue/Project):

- **Hipótese de contexto médio por turno:** ~25.000 tokens de entrada em cache (`cache_read`) + ~2.000 tokens novos de entrada (`input`) + ~800 tokens de saída (`output`) por turno, após o primeiro turno de aquecimento de cache (~25.000 tokens `cache_write` 5 min).
- **Preços de referência da API Anthropic (por milhão de tokens — MTok)** [FONTE: https://claude.com (acesso em 2026-11-15)]:
  - **Opus 5.5:** entrada `$4,00`, cache write 5m `$5,00` (1,25×), cache read `$0,20` (0,05×), saída `$20,00`.
  - **Sonnet 5.5:** entrada `$2,00`, cache write 5m `$2,50` (1,25×), cache read `$0,10` (0,05×), saída `$10,00`.

### 4.2 Comparação estimada por sessão de 20 turnos

| Configuração | Economia de tokens estimada | Custo incremental estimado por sessão (20 turnos) | Observação |
|---|---|---|---|
| **Claude Code + Opus** | Base (0%) | **$0 extra** (dentro da assinatura Claude Pro/Max/Ultra) [FONTE: https://claude.com] | Consome mais cota semanal da assinatura. |
| **Claude Code + Sonnet** | ~0%–10% em tokens (menos reasoning); ~50% menos pressão na cota [HIPÓTESE] | **$0 extra** (dentro da assinatura Claude Pro/Max/Ultra) [FONTE: https://claude.com] | Melhor relação custo-benefício sem sair da assinatura. |
| **Hermes + Sonnet (API)** | ~5%–15% em tokens de contexto inicial (`MEMORY.md` 2.200 chars + `SKILL.md` sob demanda) [HIPÓTESE] | **~$0,34 / sessão** (`$0,0625` write + `$0,0475` cache read + `$0,08` new input + `$0,16` output) [ESTIMATIVA] | Custo adicional real em USD sobre a assinatura já paga. |
| **Hermes + Opus (API)** | ~5%–15% em tokens de contexto inicial [HIPÓTESE] | **~$0,68 / sessão** (dobro do Sonnet) [ESTIMATIVA] | Mais caro que usar Claude Code na assinatura. |

**Conclusão sobre tokens e custos:** ambos os harnesses utilizam prompt caching e carregamento sob demanda de skills (`SKILL.md`). Portanto, trocar Claude Code por Hermes **não** gera uma redução estrutural massiva de tokens (~5%–15% estimados no contexto inicial), mas **introduz cobrança direta em dólar por API** que hoje não existe na assinatura do Claude Code.

---

## 5. Solução recomendada para memória entre computadores

O princípio adotado pelo projeto em [docs/AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md) e [docs/RETOMADA-MAESTRO.md](RETOMADA-MAESTRO.md) permanece o mais seguro e portável:

1. **GitHub como única fonte de verdade:**
   - Código, decisões arquiteturais e documentação técnica em `yannbastos/horus` (`main` e branches publicadas).
   - Fila operacional, prioridades, bloqueios e pontos de retomada no GitHub Project privado e nas Issues de `yannbastos/horus-management`.
2. **Skill `horus-maestro` versionada no repositório:**
   - Manter `.claude/skills/horus-maestro/SKILL.md` e `.agents/skills/horus-maestro/SKILL.md` apenas como ponteiros finos para `docs/RETOMADA-MAESTRO.md` (sem regras duplicadas), garantindo comportamento idêntico em qualquer PC após `git pull`.
3. **Memória local do Hermes (ou do Claude Code) apenas como cache efêmero de máquina:**
   - `MEMORY.md`, `USER.md`, `sessions/` e `state.db` do Hermes **não** são fonte de verdade e **nunca** devem ser versionados no GitHub.
   - `hermes_cli/backup.py` separa arquivos secretos (`_SECRET_FILE_NAMES = {".env", "auth.json", "state.db", "vault.key", "vault.json.enc"}`) [TESTADO: `hermes_cli/backup.py:169`], mas `hermes backup` ainda gera arquivos locais e **não** faz sincronização automática entre computadores [FONTE: https://hermes-agent.nousresearch.com/docs (acesso em 2026-11-15)].
4. **Sincronização segura entre PC pessoal e PC do trabalho:**
   - **Não presumir sincronização automática.**
   - Toda decisão durável ou aprendizado operacional relevante deve ser promovido manualmente (e revisado) para Markdown versionado em `yannbastos/horus` (`docs/`) ou registrado como comentário na Issue correspondente em `yannbastos/horus-management`.

---

## 6. Riscos e limitações

1. **Risco de ToS e bloqueio de conta:** tentar reutilizar token OAuth da assinatura Claude Code no Hermes viola os Termos de Uso da Anthropic e não é impedido pelo Hermes: o código instalado oferece login OAuth Pro/Max e lê credenciais do Claude Code, então o risco de bloqueio pela Anthropic é real e o cuidado cabe ao operador [FONTE: https://support.anthropic.com (acesso em 2026-11-15); TESTADO: `agent/anthropic_credentials.py`, `hermes_cli/auth.py`].
2. **Risco de gasto não previsto:** operar Hermes com `ANTHROPIC_API_KEY` ou OpenRouter sem tetos rígidos no provedor cria fatura variável em USD, além da assinatura já paga.
3. **Perda de reatividade no Orca:** sem suporte nativo a `--agent hermes` no `orca orchestration worker-start`, o Maestro perderia o wake-up automático de workers [TESTADO: `orca orchestration worker-start --help`].
4. **Contaminação entre perfil Maestro e perfil do produto (`horus`):** o Hórus já utiliza um perfil Hermes endurecido (`horus`, zero ferramentas, memória desligada) para o produto [TESTADO: `docs/integracao-hermes.md`, `docs/seguranca-hermes.md`]. Rodar o Maestro no mesmo host exige isolamento estrito de `HERMES_HOME`/perfil para nunca vazar credenciais, cron jobs ou memória para o perfil do produto.
5. **Superfície de ataque maior:** plugins, MCPs, cron e hooks do Hermes exigem auditoria contínua; como registrado em [docs/seguranca-hermes.md](seguranca-hermes.md), mesmo configurações restritivas podem ter hooks de context engine ou skills essenciais recriadas em atualizações.

---

## 7. Recomendação final e plano gradual reversível

### Recomendação: **MANTER o Claude Code no Orca como harness do Maestro, adotando uso híbrido de modelos (`Claude Code + Sonnet` por padrão e `Opus` sob demanda)**

- **Não migrar para o Hermes Agent como Maestro agora.**
- **Ação imediata recomendada (sem migração):** usar `Claude Code + Sonnet` para a rotina diária de coordenação (despacho, checagem de `git diff --stat`, atualização de Issues/Project) e reservar `Claude Code + Opus` apenas para arquitetura complexa, conflitos difíceis e revisões críticas — reduzindo consumo da cota semanal da assinatura sem custo adicional de API e preservando 100% da integração nativa com o Orca.

### Plano gradual reversível (somente se houver decisão futura de migrar para Hermes)

Caso, após a entrega do primeiro vídeo piloto e com orçamento de API aprovado pelo idealizador, decida-se testar o Hermes como Maestro:

1. **Fase 0 — Preparação isolada (reversível):** criar perfil dedicado `maestro` (separado do perfil `horus` do produto), configurar `ANTHROPIC_API_KEY` com limite mensal rígido no painel da Anthropic e desabilitar cron, gateway externo e plugins não auditados.
2. **Fase 1 — Modo sombra somente leitura:** executar `hermes` com a skill `horus-maestro` apenas para executar as etapas A–D de `docs/RETOMADA-MAESTRO.md` e propor plano de despacho, sem executar `worker-start` nem alterar Issues.
3. **Fase 2 — Piloto delimitado (1–2 Issues não críticas):** autorizar despacho via `orca orchestration` em Issues de documentação ou baixo risco, comparando consumo real em USD e ergonomia de espera sem wake-up nativo.
4. **Critério de rollback imediato:** qualquer aumento de custo acima do orçamento, falha de coordenação de workers ou conflito com o perfil `horus` encerra o piloto e retorna imediatamente ao Claude Code no Orca sem perda de estado (pois todo estado oficial permanece no Git e no GitHub Projects).

---

## 8. Limpeza do perfil temporário da PoC

O perfil isolado utilizado nesta avaliação (`%TEMP%\horus-poc-hermes-136`) foi removido ao término dos ensaios com:

```powershell
Remove-Item -Recurse -Force "$env:TEMP\horus-poc-hermes-136"
```

Nenhum arquivo do perfil `horus` do produto, do Claude Code atual ou de credenciais foi lido ou modificado.
