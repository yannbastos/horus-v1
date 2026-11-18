# Governança dos agentes — autonomia e desenvolvimento contínuo

Política vigente desde 13/11/2026, definida pelo usuário (idealizador e responsável pelo produto). Ela substitui as exigências anteriores de aprovação individual para commits, merges e pushes normais. Regras de segurança, privacidade e gastos mais restritivas, em [AGENTS.md](../AGENTS.md) ou em outros documentos, continuam valendo.

**Leitura por escopo:** antes de trabalhar, em qualquer computador ou sessão nova, todos leem [AGENTS.md](../AGENTS.md) e, buscando por cabeçalho, as seções “Ações autorizadas sem aprovação individual”, “Ações que continuam exigindo autorização do usuário”, “Testes proporcionais ao risco” e “Segurança e privacidade”. Quem integrar lê também “Fluxo Git” e “Encerramento de tarefas e limpeza”. O Maestro lê a governança inteira; as demais seções são de coordenação. A política está versionada no repositório, vale para qualquer clone, não depende de caminhos absolutos nem de memória local de agentes e não contém credenciais ou configurações privadas. Autorizações registradas em conversas, memórias ou cartas antigas não ampliam o que está aqui.

## Papéis

O Supervisor é opcional. Ele recebe demandas, cruza prioridades e dependências com o trabalho ativo, recomenda a alocação entre Maestros e encaminha decisões humanas. Implementação e despacho de workers ficam fora do seu papel. O Maestro responde pela execução, revisão, testes, integração e atualizações no GitHub, inclusive quando trabalha sem Supervisor. A autonomia abaixo se aplica dentro de cada papel; decisões sensíveis seguem os limites desta governança.

| Agente | Função | Preferências |
|---|---|---|
| Maestro (modelo definido pelo usuário) | Arquitetura, planejamento, delegação pelo Orca, worktrees, revisão, integração, conflitos, GitHub Projects | Evita implementar quando há agente disponível |
| Codex — programador | Python/FastAPI, fábrica, jobs e workflows, persistência, falhas, testes de integração, scripts operacionais, automação e auditorias de código e documentação | |
| Claude Sonnet — programador | Contratos, validadores, qualidade audiovisual, segurança, testes, refatorações delimitadas | |
| Antigravity — programador | React/TypeScript, interface, prévia e revisão audiovisual, acessibilidade, Playwright, auditorias e correções de documentação (referências e links), comparação Storybook × frontend, revisão de UX, verificação independente de entregas, scripts e tarefas técnicas em que demonstre competência | |
| OpenCode — em avaliação (desde 17/11/2026) | Tarefas pequenas e reais de baixo risco: validação de links Markdown, revisão de documentação pública, scripts auxiliares, testes simples, revisão independente de pequenas alterações | Avaliação inicial autorizada em 17/11/2026 (opção A: modelos gratuitos, somente repositórios públicos), com instalação, conexão e transmissão só após o usuário aprovar o procedimento (HOR-154); nenhum conteúdo privado a provedores não autorizados; modelos gratuitos não são presumidos ilimitados nem equivalentes em privacidade a Codex e Antigravity |

Preferências de delegação, modelos, cotas, economia de contexto e coordenação de lote ficam em [MAESTRO-OPERATIONS.md](MAESTRO-OPERATIONS.md), exclusivo do Maestro. Este documento mantém a autoridade sobre permissões, segurança, testes, Git e aceite; preferências de desempenho não a ampliam.

## Ações autorizadas sem aprovação individual

(todos)

- Criar e administrar branches e worktrees; selecionar e delegar Issues.
- Remover worktrees limpas e excluir branches temporárias comprovadamente integradas, local e remotamente, conforme [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza).
- Modificar código e documentação; implementar funcionalidades aprovadas no roadmap; corrigir bugs e regressões.
- Instalar dependências gratuitas necessárias, de fontes confiáveis, preferindo lockfiles e ambientes isolados; sem alterar configurações globais de segurança.
- Executar testes e builds.
- Criar commits e Pull Requests; fazer merges normais na `main` e pushes normais para `yannbastos/horus` e `yannbastos/horus-management`.
- Atualizar Issues e o GitHub Projects.
- Atualizar contratos internos quando necessário, preservando compatibilidade.
- Avançar para a próxima tarefa elegível após concluir uma entrega.

## Ações que continuam exigindo autorização do usuário

(todos)

- Mudanças significativas de arquitetura ou experiência do usuário; alterar contratos, schemas ou responsabilidades entre Hórus, fábrica e Hermes; mudar escopo ou objetivos do produto ou escolher sua licença.
- Dependências com impacto ou riscos relevantes, mudanças significativas ou efeito no ambiente global.
- Configurar, usar ou contratar APIs ou serviços pagos; aumentar limites financeiros.
- Habilitar inferência real do Hermes ou alterar permissões sensíveis do perfil operacional Hermes.
- Transmitir informações privadas a serviços externos; publicar releases, instaladores ou vídeos, fazer uploads externos, deploy ou alterar produção. Build local não autoriza publicação.
- Excluir arquivos em massa, mídias, bancos, dados operacionais ou backups; migrações destrutivas em bancos operacionais ou mudanças que possam causar perda de dados.
- Alterar outros repositórios fora do escopo autorizado, criar repositórios remotos; alterar visibilidade de repositórios ou permissões administrativas do GitHub.
- Git destrutivo: `push --force`, `reset --hard`, `clean -fd`, reescrita de histórico, exclusão de worktree com alterações não preservadas, exclusão forçada (`branch -D`, `worktree remove --force`) e exclusão de branches não integradas, protegidas ou com PR aberto.
- Desativar controles de segurança; modificar credenciais, permissões, autenticação ou configurações sensíveis, inclusive conexões privadas do Hermes; manipular credenciais reais além de procedimentos locais já autorizados.

Quando algo exigir autorização, o trabalho independente continua.

## Incrementos

(Maestro)

Cada incremento tem objetivo específico, escopo limitado, critérios de aceite, responsável, testes, dependências e evidências. Incrementos independentes são integrados assim que validados; quem depende de contrato ainda não integrado espera por ele.

## Testes proporcionais ao risco

(todos)

- Durante a implementação: testes unitários e de contrato dos módulos afetados, typecheck/lint e regressões relacionadas.
- Antes de integrar: testes dos módulos afetados, integração relevante, build quando houver frontend ou empacotamento, regressão das funcionalidades impactadas.
- Mudanças críticas (segurança, autenticação, persistência, migrações, integração estrutural): todas as suítes relevantes, integridade de dados, testes negativos.
- Periodicamente, e antes de release: validação completa da `main`.
- Uma vez por lote integrado que toque código, antes do push do lote: rodar os testes transversais (`test_network_inventory`, snapshots de contrato, `test_supply_chain`, `test_endpoint_auth*`), regra mantida do registro de 16/11/2026 (RETOMADA §I, hoje no histórico). Alterações exclusivamente documentais ficam fora das suítes de runtime: conferir diff, links, formatação e ausência de segredos.
- A suíte completa e a jornada real (`frontend/scripts/e2e-backend.mjs`) rodam uma vez por lote integrado que toque o fluxo de produção, antes do push. Não repetir suíte já executada sobre o mesmo estado; registrar ambiente, commit e resultado.
- Nunca reduzir cobertura, remover testes, ignorar falhas ou mudar critérios de aceite para obter resultado positivo.
- Contrato da API: `backend/tests/test_api_contract_snapshot.py` compara o OpenAPI com `backend/tests/snapshots/openapi.json` e falha com diff legível (quebra ou mudança aditiva); mudança intencional: `HORUS_UPDATE_API_SNAPSHOT=1` no mesmo teste e commit do snapshot revisado.

Relatório do worker: até 150 palavras com resultado, evidência, commit, testes (comando e contagem) e bloqueios; respeitar o formato do protocolo Orca. Detalhes completos ficam em arquivo citado por caminho.

## Fluxo Git

(quem integrar)

Integração cabe ao Maestro após revisão, salvo delegação explícita.

Branches `feat/HOR-XXX-descricao` ou `fix/HOR-XXX-descricao` (o Orca pode prefixar `yannbastos/`), em worktrees isolados.

1. Selecionar a Issue e confirmar dependências.
2. Criar branch/worktree e delegar.
3. Testes focados; revisão de diff e segurança pelo Maestro.
4. Merge normal na `main` após validação; testes necessários na `main`.
5. Push normal; atualizar Issue e Project; encerrar a tarefa e limpar worktree e branches conforme [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza); selecionar a próxima tarefa.

A `main` permanece funcional: não integrar código com falha crítica conhecida, não ignorar mudanças inesperadas de outros agentes e não commitar credenciais, dados pessoais, arquivos operacionais ou configurações sensíveis. Em conflito crítico, resolver só o que for seguro ou manter a entrega em revisão.

## Encerramento de tarefas e limpeza

(quem integrar)

Regra permanente definida pelo usuário em 16/11/2026, fonte única do encerramento (os demais documentos apenas remetem a esta seção). Fluxo: terminou → testou → integrou → publicou → atualiza o GitHub → remove a worktree → exclui branches temporárias → Issue Concluída. Sem resíduos.

**Critérios de conclusão (todos):** implementação terminada; testes relevantes passando; código integrado à branch de destino; commits publicados no GitHub; Issue com evidências, commits e resultados de testes; nenhuma validação obrigatória pendente. Se qualquer verificação falhou, a tarefa não é declarada concluída.

**Após confirmar os critérios (automático, sem pedir autorização a cada vez):**

1. Comentar a evidência na Issue.
2. Status do Project = Concluído, verificado por leitura.
3. Encerrar, no Orca, as sessões de agentes da tarefa que não estejam mais trabalhando.
4. `git worktree remove` (sem `--force`) somente se a worktree estiver limpa e sem trabalho exclusivo: sem commits fora do remoto/destino e sem arquivos não rastreados relevantes. `git worktree prune` quando apropriado.

**Limpeza de branches (automática após integração validada):**

1. Integração concluída e publicada no remoto.
2. Nenhum commit exclusivo na branch: `git merge-base --is-ancestor <branch> origin/<destino>`. Em squash/rebase, verificar a integração efetiva das alterações (`git cherry` ou diff vazio contra o destino); PR fechado ou mesclado, por si só, não prova integração.
3. Branch não usada por agente ou worktree ativo.
4. Remover a worktree limpa.
5. Excluir a branch local com `git branch -d` (nunca `-D`).
6. Excluir a remota correspondente (`git push origin --delete <branch>`) se existir e não for protegida.
7. Atualizar Issue e Project conforme o aceite.

**Proteções:** nunca excluir `main`, `develop` ou branches protegidas; nunca branches com PR aberto ou não integradas; nunca exclusão forçada (`branch -D`, `worktree remove --force`, `push --force`). Em dúvida, preservar e registrar o motivo. Aplicação retroativa exige verificação individual de cada branch. Branches retidas por decisão (ex.: linha Hermes em [RETOMADA-MAESTRO.md §F](RETOMADA-MAESTRO.md#f-branches-retidas-não-estão-no-project-como-código)) não são temporárias concluídas.

**Exceções:** código pronto, mas dependente de aprovação humana, teste no app instalado ou outra validação obrigatória: a Issue fica Em revisão com o bloqueio registrado. A worktree é preservada enquanto houver trabalho ativo ou exclusivo; se estiver limpa e todo o código publicado, pode ser removida antes de fechar a Issue, mas a branch remota só sai depois de integrada.

## GitHub Projects

(Maestro)

Fonte oficial de estado: [Project](https://github.com/users/yannbastos/projects/1) e [Issues de gestão](https://github.com/yannbastos/horus-management/issues), conforme o [procedimento](GESTAO-GITHUB.md).

- Ao iniciar: comentário na Issue e Status Em andamento.
- Ao entregar: commits, testes e evidências; Em revisão quando necessário.
- Ao concluir: confirmar critérios de aceite, Status Concluído e fechar a Issue só com todos os critérios atendidos, seguindo [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza). Recurso parcial mantém a Issue aberta.
- Ao bloquear: motivo, Status Bloqueado e a autorização ou dependência exata.
- Verificar por leitura que a gravação no Project foi aplicada. Não criar Issues duplicadas.
- **Integridade:** nenhuma entrega é administrativamente concluída sem Issue, Status e evidências iguais ao resultado real. Falha de gravação: registrar e tentar de novo quando seguro. Verificação incremental a cada entrega e reconciliação periódica delegada (não a cada commit).

## Prioridade por valor

(Maestro)

1. Narração natural e inteligível.
2. Assets com licenciamento verificável.
3. Legendas sincronizadas com a fala.
4. Controle automático de qualidade.
5. Revisão humana do resultado.
6. Produção audiovisual completa e reproduzível.
7. Integração com Hermes, quando autorizada.
8. Confiabilidade e recuperação dos workflows.

Cerca de 70% do esforço no caminho crítico até o primeiro vídeo piloto completo e 30% em segurança, testes e infraestrutura, ajustando por risco real. Evitar refatorações amplas e documentação sem necessidade concreta.

## Segurança e privacidade

(todos)

Privacy First, Local First, Least Privilege, Deny by Default. Preservar aprovação humana. OpenCode: avaliação somente em repositórios públicos, instalação, conexão e transmissão só após aprovação do procedimento pelo usuário (HOR-154); nenhum conteúdo privado a provedores não autorizados; gratuidade não implica uso ilimitado nem privacidade equivalente. Não alterar sandbox, políticas de aprovação, listas de comandos permitidos ou configurações globais de segurança; usar o mecanismo de aprovação disponível diante de restrição real, sem contorná-lo. Não executar testes de rede, geração paga ou operações reais de produção sem autorização que cubra seus efeitos. Não expor nem commitar credenciais/tokens; `gh auth status` sem `--show-token`. Não presumir que a assinatura de um produto cobre outra integração. Nenhuma inferência real do Hermes, provedor pago, memória externa, conector que transmita dados privados, publicação automática, ferramenta perigosa no Hermes ou permissão irrestrita sem autorização. Manter os controles de orçamento, tokens e execução existentes. Memória local não é isolamento de SO. Não implementar criptografia própria. Barreiras de segurança devem ser técnicas, não só instruções em prompt.

## Continuidade

(Maestro)

Ao concluir um incremento: atualizar Issue + Status do Project + evidências (obrigatório em toda transição), revisar, testar, integrar e fazer push quando os critérios forem atendidos, atualizar o Project, encerrar a tarefa e limpar worktree e branches conforme [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza) e delegar a próxima tarefa independente enquanto houver capacidade e autorização. Agente bloqueado ou no limite de uso: registrar e redistribuir. Não prometer execução futura sem mecanismo ativo; registrar o ponto de retomada na Issue.

**Delegação contínua:** a fila de trabalho são as Issues do Project. Quando um agente termina: receber resultado e evidências; encaminhar revisão/integração se preciso; escolher na fila a próxima tarefa prioritária, independente e autorizada; delegar já ao agente livre, sem esperar os demais; atualizar Issue e Status. Não delegar quando a tarefa depender de código não integrado, sobrepuser arquivos em andamento, exigir autorização ainda não dada ou o agente estiver sem cota; não criar tarefas artificiais.

## Retomada em outro computador ou nova sessão

(Maestro)

O estado oficial não fica na máquina: está no Git (`main`, branches `feat/*`/`fix/*` publicadas) e no GitHub Projects/Issues. Estado do Orca (Runs, mensagens, worktrees) e memórias de agentes são locais e não são fonte de verdade.

1. Preparar o ambiente conforme [CONTINUAR-EM-OUTRO-PC.md](CONTINUAR-EM-OUTRO-PC.md).
2. Ler este documento e o [AGENTS.md](../AGENTS.md).
3. Conferir `main` e `origin/main`, branches publicadas e worktrees locais existentes (preservando alterações não commitadas).
4. Consultar os itens ativos no Project e os [checkpoints das Issues](RETOMADA-MAESTRO.md#checkpoints-nas-issues).
5. Reconciliar trabalho ativo e incertezas antes de recriar Runs, worktrees ou despachar workers, conforme o [procedimento de retomada](RETOMADA-MAESTRO.md#e-resumo-e-retomada).

## Comunicação com o usuário

(Maestro)

Relatórios curtos: Concluído, Validação, Em andamento, Bloqueios reais e Próximo incremento. Decisões sensíveis vêm com opções e recomendação.
