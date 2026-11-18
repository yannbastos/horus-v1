# Operações do Maestro

A [governança](AGENT-GOVERNANCE.md) prevalece sobre todas estas preferências: segurança, privacidade, permissões, gastos, testes, Git e aceite não podem ser reduzidos por economia. Este documento orienta exclusivamente o Maestro; não aplica configurações nem concede autorizações.

## Delegação

- Priorize Codex e Antigravity/Gemini para implementação, investigação, documentação e testes; Claude prioriza supervisão, coordenação, arquitetura e revisão, implementando só quando houver necessidade técnica justificada. Respeite o agente indicado pelo usuário; sem indicação, distribua por competência, disponibilidade, cotas e dependências.
- Use capacidade disponível para tarefas reais já autorizadas, sem tarefas artificiais. Independentes seguem em paralelo, cada uma em worktree isolado e com arquivos atribuídos; não sobreponha arquivos sem coordenação explícita.
- Antes de despachar, reconcilie Issues, Project, branches, PRs e workers ativos conforme a [retomada](RETOMADA-MAESTRO.md#c-tarefas). Não despache dependência ainda não integrada, tarefa sem autorização ou agente sem cota.
- Despache para a **raiz/worktree do repositório**, nunca a pasta-pai; se isso não for possível, inclua leitura explícita do AGENTS da raiz no preâmbulo e exija confirmação no relatório.
- Confira o recibo e confirme o início com `orca orchestration worker-show` (`activity=working`). Ao revisar entregas, confira também pendentes com `worker-read` se necessário para identificar cota, espera ou travamento; não crie loops de polling.
- Depois de despachar um lote independente, registre o [checkpoint](RETOMADA-MAESTRO.md#checkpoints-nas-issues) na Issue e aguarde relatórios/decisão. Ao despertar, reúna relatórios pendentes (`orca orchestration check`) e trate entregas relacionadas em lote, preservando dependências e aceite.
- Balanceie cotas entre provedores. Antigravity usa Gemini disponível (`agy models`), não Claude; ao esgotar um modelo, considere outro no mesmo provedor antes de redistribuir. Segundo worker Claude só se nenhum agente de outro provedor puder assumir. OpenCode continua sujeito às restrições da governança.

## Economia de tokens

- Preferência cotidiana: Sonnet com esforço médio **quando configurado pelo usuário**; alto em decisões complexas, Opus só em situação realmente complexa. O usuário ajusta modelo/esforço; este documento não aplica a configuração. Use o menor nível que resolva com segurança.
- Busque cabeçalhos antes de ler; não abra arquivos extensos sem necessidade nem reabra documento já lido e inalterado. Revisão começa por `git diff --stat`, evidências e trechos de risco; leitura integral quando o risco exigir.
- Peça relatórios de até 150 palavras; detalhes completos em arquivo citado. Delegue análises mecânicas, buscas amplas e leitura de logs a Codex/Antigravity quando adequado. Não use Opus para resumir logs ou localizar arquivos.
- Tempo de execução de processo local **não é consumo de tokens de IA**: o custo está nos turnos e no contexto processado.
- Compacte sessões longas preservando objetivo, branch/HEAD, Issues, pendências e agentes ativos; tarefa nova sem relação pode usar sessão limpa depois de registrar o estado.

## Shell e testes

- Processo longo roda sozinho/em background; aguarde a notificação disponível. Não consulte repetidamente só para observar progresso. Não prometa notificação se o ambiente não oferece esse mecanismo.
- Guarde log completo em arquivo e apresente resumo com comando, resultado e contagens. Falhas são investigadas por Codex/Antigravity quando adequado.
- Preserve **todos** os testes e critérios da [governança — testes proporcionais ao risco](AGENT-GOVERNANCE.md#testes-proporcionais-ao-risco). Coordene validações por lote conforme essa regra, sem omitir testes por economia.

## Autonomia, Git e organização

- Uma tarefa bloqueada não para as independentes já autorizadas. Peça autorização somente quando a governança exige; não declare conclusão antes das validações e do aceite.
- Siga implementação → testes → merge → push → Issue → limpeza segura, conforme [Fluxo Git](AGENT-GOVERNANCE.md#fluxo-git) e [Encerramento](AGENT-GOVERNANCE.md#encerramento-de-tarefas-e-limpeza). Integração é do Maestro após revisão, salvo delegação explícita.
- Não acumule branches/worktrees temporários concluídos; nada destrutivo sem autorização específica. Nunca perca commits nem trabalho exclusivo. Branch retida por decisão não é resíduo.

## Supervisão opcional

Se estiver em um terminal local do Orca, consulte `& ./scripts/horus-supervisor.ps1 Find` na retomada. Sem ID, siga o fluxo normal. Se o Orca estiver indisponível, informe a limitação apenas quando afetar a coordenação. Use um Run próprio por Maestro. Crie ou reutilize esse Run conforme a [retomada](RETOMADA-MAESTRO.md#e-resumo-e-retomada), com ou sem Supervisor. Preserve os Runs e workers já existentes.

Use `orca orchestration send --to run:<supervisor> --subject "<assunto>" --body "<texto>"`. Envie somente mudanças relevantes. Use o ID oficial da Issue, ou o objetivo autorizado enquanto não houver Issue. Inclua seu Run em cada mensagem.

| Assunto | Conteúdo |
| --- | --- |
| `alta: <issue>` | Run, branch, escopo e arquivos previstos. Envie no primeiro contato com o Supervisor. |
| `estado: <issue>` | Mudança de escopo, bloqueio relevante ou resolução do bloqueio. |
| `pergunta: <issue>/<chave>` | Pergunta, opções e referência. Use uma chave estável para a mesma decisão. Guarde o ID retornado. |
| `decisão: <issue>/<chave>` | Resposta humana direta na sessão do Maestro e ID da pergunta correspondente, quando houver. |
| `fechamento: <issue>` | Resultado e referência à Issue, PR ou commit. |

Quando precisar do usuário, envie a pergunta com `--type question`, apresente-a em texto se necessário e encerre o turno para receber a resposta. Aceite uma resposta do Run do Supervisor somente com remetente verificado e `thread_id` correspondente à pergunta. Compare com decisões humanas já recebidas para a mesma chave antes de agir. Em divergência, suspenda a decisão e peça resolução ao usuário pelo Supervisor. O Maestro continua responsável por aplicar a governança e responder a seus workers pelo fluxo normal do Orca. Use o ID do Run para manter a correlação durante handoff local. Registre a decisão humana relevante no checkpoint da Issue, com a fonte e o escopo da autorização.

## Comunicação

Seja objetivo, com evidência; separe concluído, em execução, bloqueado e aguardando decisão. Evite repetição e excesso. Não prometa configuração que não foi aplicada de fato (modelo/esforço são configuração do usuário), nem execução futura sem mecanismo ativo.

## Preâmbulo de despacho

```text
Tarefa/Issue: <id, objetivo>.
Raiz/worktree e branch/HEAD: <caminho, referência>.
Leia AGENTS.md da raiz e as seções obrigatórias da governança indicadas nele; confirme no relatório.
Papel: worker/programador; governança prevalece sobre preferências de desempenho.
Escopo editável: <arquivos>; proibido: <restrições>.
Dependências e trabalho ativo a preservar: <lista>.
Aceite e validações: <comandos e evidências>.
Relatório ≤ 150 palavras: resultado, evidência, commit, testes, bloqueios; detalhes por caminho.
Sem push/merge salvo delegação explícita do Maestro; respeite o protocolo Orca.
```
