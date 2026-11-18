# Operações do Maestro

A [governança](AGENT-GOVERNANCE.md) prevalece sobre todas estas preferências: segurança, privacidade, permissões, gastos, testes, Git e aceite não podem ser reduzidos por economia. Este documento orienta exclusivamente o Maestro; não aplica configurações nem concede autorizações.

## Delegação

- Priorize Codex e Antigravity/Gemini para implementação, investigação, documentação e testes; Claude prioriza supervisão, coordenação, arquitetura e revisão, implementando só quando houver necessidade técnica justificada. Respeite o agente indicado pelo usuário; sem indicação, distribua por competência, disponibilidade, cotas e dependências.
- Use capacidade disponível para tarefas reais já autorizadas, sem tarefas artificiais. Independentes seguem em paralelo, cada uma em worktree isolado e com arquivos atribuídos; não sobreponha arquivos sem coordenação explícita.
- Antes de despachar, reconcilie Issues, Project, branches, PRs e workers ativos conforme a [retomada](RETOMADA-MAESTRO.md#c-tarefas). Não despache dependência ainda não integrada, tarefa sem autorização ou agente sem cota.
- Confira antes do despacho se o diretório de execução do worker é a raiz do worktree da tarefa (`git rev-parse --show-toplevel`). Isso é obrigatório para carregar `AGENTS.md` no Antigravity. Se o Orca só oferecer a pasta-pai, corrija o workspace antes de despachar. O worker confirma a leitura no relatório.
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

- Para falhas relevantes, siga a [regra de correção e prevenção](AGENT-GOVERNANCE.md#testes-proporcionais-ao-risco) e registre causa e evidência na Issue. Um worker sem rede entrega as evidências ao Maestro, que publica a atualização e confere a gravação. Compare a integração local com a remota antes de reportar publicação ou conclusão. Se o Maestro integrar por outra via após um dispatch falhar, preserve o resultado histórico da tentativa e registre a integração separadamente no checkpoint, com SHA e testes. Uma task Orca descreve a tentativa local; o aceite da Issue exige evidência no Git e no Project.
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

## Decisões humanas

Antes de apresentar uma decisão ao usuário, o Maestro preenche esta verificação:

1. [ ] Consulte os [eventos da Issue](RETOMADA-MAESTRO.md#eventos-de-coordenação-nas-issues) pela chave estável. Se já estiver respondida ou cancelada, confira a decisão vigente e encerre a consulta. Se for nova, publique o evento aberto com opções, recomendação e responsável. Confirme a gravação. A chave pertence à Issue e pode ser reaberta somente por um novo evento com outra chave e justificativa.
2. [ ] No terminal do Orca, use `& ./scripts/horus-supervisor.ps1 Find`. Sem Supervisor confirmado, o Maestro pode apresentar a pendência diretamente após verificar que não há outro responsável apresentando a mesma questão. Se a inspeção falhar ou o Supervisor for conhecido mas estiver inacessível, registre falha de encaminhamento e informe o impedimento técnico. Retome o envio após a reconciliação ou combine explicitamente o uso do modo sem Supervisor. Uma sessão iniciada fora do Orca só usa o fluxo direto se o usuário a designou para trabalhar de forma independente.
3. [ ] Com Supervisor ativo, comprove o próprio Run com `& ./scripts/horus-supervisor.ps1 VerifyRun` em um terminal do worktree. Procure `pergunta: HOR-XXX/chave` na caixa de destino antes de enviar com `--type question`. Guarde o ID devolvido e confirme a entrega:

   ```powershell
   $subject = 'pergunta: HOR-XXX/chave'
   orca orchestration send --to "run:$supervisor" --subject $subject --type question --body $resumo --full --json
   & ./scripts/horus-supervisor.ps1 VerifyDelivery -RunId $supervisor -FromRunId $maestro -MessageId $msg -Type question -Subject $subject
   ```

4. [ ] Somente após confirmar entrega e registro, informe o usuário de que a pendência está com o Supervisor. Quem apresenta pela primeira vez registra a apresentação em `evidence` no evento aberto da mesma chave. O Supervisor agrupa as pendências quando acorda e referencia as que já foram apresentadas. O Maestro encerra o turno para receber a resposta. Se o usuário pedir os detalhes diretamente, use a mesma chave e registre a apresentação para evitar duplicidade.

Para enviar resposta, decisão direta ou confirmação de recebimento, use `--type status --thread-id <id-da-pergunta>`. Os assuntos são `resposta:`, `decisão:` e `recebida:`, seguidos de `HOR-XXX/chave`. `decision_gate` pertence ao fluxo de workers e pode rejeitar o Supervisor como remetente. Use `VerifyDelivery` novamente com os Runs na direção do envio, `-Type status` e `-ThreadId $idDaPergunta`. A verificação confere os atuais coordenadores, suas raízes Git, a caixa de destino, a unicidade e a pergunta original entre o mesmo par de Runs.

A sessão que recebeu a decisão humana consulta o histórico e publica uma única transição para `respondida` com autor, alcance e evidência, ou `cancelada` com motivo. Se recebeu no Supervisor, encaminhe `resposta:` ao Run verificado do Maestro. O Maestro confere a mesma decisão na Issue, verifica a entrega e devolve `recebida:` no mesmo thread. Se recebeu no Maestro, encaminhe `decisão:` ao Supervisor e este encerra a apresentação consultando a Issue. Se a decisão já estiver registrada, reutilize-a e confira a entrega antes de qualquer envio. Em caso de recibo incerto, confira ambas as caixas ou use o retry do Orca com o mesmo identificador da operação. Decisões divergentes suspendem a execução até resolução humana.

Um Run em contexto de pasta ancestral não prova sua origem Git. Para o fluxo local, use um terminal vinculado a um worktree do Hórus. Se a sessão antiga estiver fora de worktree, confirme os dispatches e a frente na Issue, preserve o Run e coordene a transição com o responsável. A decisão durável pode ser recuperada pelo GitHub sem reenviar por um thread de outra instalação. Se o handoff ou o limite da caixa impedir a verificação, mantenha a decisão na Issue e confirme com o responsável antes de agir. IDs de Runs e mensagens ficam locais.

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
