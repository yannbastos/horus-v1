# Governança dos agentes — autonomia e desenvolvimento contínuo

Política vigente desde 13/11/2026, definida pelo usuário (idealizador e responsável pelo produto). Ela substitui as exigências anteriores de aprovação individual para commits, merges e pushes normais. Regras de segurança, privacidade e gastos mais restritivas, em [AGENTS.md](../AGENTS.md) ou em outros documentos, continuam valendo.

**Leitura obrigatória:** todo agente lê este documento e o [AGENTS.md](../AGENTS.md) antes de trabalhar, em qualquer computador e em qualquer sessão nova. A política está versionada no repositório, vale para qualquer clone, não depende de caminhos absolutos nem de memória local de agentes e não contém credenciais ou configurações privadas. Autorizações registradas em conversas, memórias ou cartas antigas não ampliam o que está aqui.

## Papéis

| Agente | Função | Preferências |
|---|---|---|
| Maestro (modelo Claude definido pelo usuário; desde 17/11/2026, Claude Sonnet 5.5 com esforço alto em experimentação, Opus reservado a situações realmente complexas) | Arquitetura, planejamento, delegação pelo Orca, worktrees, revisão, integração, conflitos, GitHub Projects | Evita implementar quando há agente disponível |
| Codex — programador | Python/FastAPI, fábrica, jobs e workflows, persistência, falhas, testes de integração, scripts operacionais, automação e auditorias de código e documentação | |
| Claude Sonnet — programador | Contratos, validadores, qualidade audiovisual, segurança, testes, refatorações delimitadas | |
| Antigravity — programador | React/TypeScript, interface, prévia e revisão audiovisual, acessibilidade, Playwright, auditorias e correções de documentação (referências e links), comparação Storybook × frontend, revisão de UX, verificação independente de entregas, scripts e tarefas técnicas em que demonstre competência | |
| OpenCode — em avaliação (desde 17/11/2026) | Tarefas pequenas e reais de baixo risco: validação de links Markdown, revisão de documentação pública, scripts auxiliares, testes simples, revisão independente de pequenas alterações | Só após configuração de provedor autorizada pelo usuário; nenhum conteúdo privado a provedores não autorizados; modelos gratuitos não são presumidos ilimitados nem equivalentes em privacidade a Codex e Antigravity |

Codex, Claude Sonnet e Antigravity são os programadores preferenciais (Antigravity/Gemini e Codex recebem a maior parte da implementação, auditoria e verificação; Claude Sonnet só quando necessário; OpenCode participa em avaliação); as preferências não são limites rígidos e a distribuição pode mudar conforme a disponibilidade e os limites de uso de cada um. O Maestro redistribui por dependência, disponibilidade e limite de uso, não deixa agente ocioso quando há tarefa independente autorizada e não inicia tarefas simultâneas nos mesmos arquivos sem coordenação explícita (cada tarefa declara os arquivos de que é dona).

**Distribuição automática (regra permanente):** tarefa enviada sem agente indicado é distribuída pelo Maestro a Codex, Claude Sonnet ou Antigravity/Gemini conforme competência, disponibilidade, cotas e dependências, em paralelo quando independentes e sem arquivos em comum. Agente indicado pelo usuário é respeitado. O Maestro coordena, delega, acompanha, revisa e integra; só executa pessoalmente quando houver necessidade técnica justificada (ex.: correção trivial bloqueando uma integração).

## Ações autorizadas sem aprovação individual

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

- Configurar, usar ou contratar APIs ou serviços pagos; aumentar limites financeiros.
- Habilitar inferência real do Hermes ou alterar permissões sensíveis do perfil operacional Hermes.
- Transmitir informações privadas a serviços externos; publicar vídeos ou fazer uploads externos.
- Excluir dados operacionais ou backups; migrações destrutivas em bancos operacionais.
- Alterar visibilidade de repositórios ou permissões administrativas do GitHub.
- Git destrutivo: `push --force`, `reset --hard`, `clean -fd`, reescrita de histórico, exclusão de worktree com alterações não preservadas, exclusão forçada (`branch -D`, `worktree remove --force`) e exclusão de branches não integradas, protegidas ou com PR aberto.
- Desativar controles de segurança; manipular credenciais reais além de procedimentos locais já autorizados.

Quando algo exigir autorização, o trabalho independente continua.

## Incrementos

Cada incremento tem objetivo específico, escopo limitado, critérios de aceite, responsável, testes, dependências e evidências. Incrementos independentes são integrados assim que validados; quem depende de contrato ainda não integrado espera por ele.

## Testes proporcionais ao risco

- Durante a implementação: testes unitários e de contrato dos módulos afetados, typecheck/lint e regressões relacionadas.
- Antes de integrar: testes dos módulos afetados, integração relevante, build quando houver frontend ou empacotamento, regressão das funcionalidades impactadas.
- Mudanças críticas (segurança, autenticação, persistência, migrações, integração estrutural): todas as suítes relevantes, integridade de dados, testes negativos.
- Periodicamente, e antes de release: validação completa da `main`.
- Ao integrar na `main`: rodar também os testes transversais (`test_network_inventory`, snapshots de contrato, `test_supply_chain`, `test_endpoint_auth*`), regra mantida do registro de 16/11/2026 (RETOMADA §I, hoje no histórico).
- Nunca reduzir cobertura, remover testes, ignorar falhas ou mudar critérios de aceite para obter resultado positivo.
- Contrato da API: `backend/tests/test_api_contract_snapshot.py` compara o OpenAPI com `backend/tests/snapshots/openapi.json` e falha com diff legível (quebra ou mudança aditiva); mudança intencional: `HORUS_UPDATE_API_SNAPSHOT=1` no mesmo teste e commit do snapshot revisado.

## Fluxo Git

Branches `feat/HOR-XXX-descricao` ou `fix/HOR-XXX-descricao` (o Orca pode prefixar `yannbastos/`), em worktrees isolados.

1. Selecionar a Issue e confirmar dependências.
2. Criar branch/worktree e delegar.
3. Testes focados; revisão de diff e segurança pelo Maestro.
4. Merge normal na `main` após validação; testes necessários na `main`.
5. Push normal; atualizar Issue e Project; encerrar a tarefa e limpar worktree e branches conforme [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza); selecionar a próxima tarefa.

A `main` permanece funcional: não integrar código com falha crítica conhecida, não ignorar mudanças inesperadas de outros agentes e não commitar credenciais, dados pessoais, arquivos operacionais ou configurações sensíveis. Em conflito crítico, resolver só o que for seguro ou manter a entrega em revisão.

## Encerramento de tarefas e limpeza

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

Fonte oficial de estado: [Project](https://github.com/users/yannbastos/projects/1) e [Issues de gestão](https://github.com/yannbastos/horus-management/issues), conforme o [procedimento](GESTAO-GITHUB.md).

- Ao iniciar: comentário na Issue e Status Em andamento.
- Ao entregar: commits, testes e evidências; Em revisão quando necessário.
- Ao concluir: confirmar critérios de aceite, Status Concluído e fechar a Issue só com todos os critérios atendidos, seguindo [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza). Recurso parcial mantém a Issue aberta.
- Ao bloquear: motivo, Status Bloqueado e a autorização ou dependência exata.
- Verificar por leitura que a gravação no Project foi aplicada. Não criar Issues duplicadas.
- **Integridade:** nenhuma entrega é administrativamente concluída sem Issue, Status e evidências iguais ao resultado real. Falha de gravação: registrar e tentar de novo quando seguro. Verificação incremental a cada entrega e reconciliação periódica delegada (não a cada commit).

## Prioridade por valor

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

Privacy First, Local First, Least Privilege, Deny by Default. Nenhuma inferência real do Hermes, provedor pago, memória externa, conector que transmita dados privados, publicação automática, ferramenta perigosa no Hermes ou permissão irrestrita sem autorização. Manter os controles de orçamento, tokens e execução existentes. Memória local não é isolamento de SO. Não implementar criptografia própria. Barreiras de segurança devem ser técnicas, não só instruções em prompt.

## Uso dos modelos

Maestro (modelo definido pelo usuário; desde 17/11/2026, Claude Sonnet 5.5 com esforço alto em experimentação; Opus só para situações realmente complexas) para coordenação, arquitetura complexa e revisão crítica; Antigravity/Gemini para frontend, testes visuais, auditorias e correções de documentação, verificação independente e scripts; Codex para backend, integrações, scripts operacionais, testes e auditorias; Claude Sonnet para implementação só quando necessário; OpenCode em avaliação. Modelos rápidos para tarefas simples. Não presumir que a assinatura de um produto cobre o uso por outra integração.

## Eficiência de consumo

Maximizar progresso real por unidade de consumo, sem reduzir qualidade ou segurança.

- **Maestro coordena, não implementa nem lê implementações extensas.** Revisão por `git diff --stat` e trechos dos arquivos de risco (auth, migrações, aprovação, rede, fluxo de produção); leitura completa só quando o risco exigir. Investigações e buscas amplas são delegadas.
- **Tarefa delegada:** objetivo, issue, arquivos permitidos/proibidos e evidência esperada. Regras comuns não são repetidas: a tarefa cita esta política.
- **Relatório do programador (`worker_done`):** 150–300 palavras com issue, resultado, arquivos, commit, testes (comando e contagem), problemas e bloqueios. Detalhes longos ficam num arquivo de relatório ou log citado pelo caminho, nunca descartados.
- **Testes:** focados durante a implementação, com saída resumida; antes do merge, os relacionados e o build relevante. A suíte completa e a jornada real (`frontend/scripts/e2e-backend.mjs`) rodam **uma vez por lote integrado** que toque o fluxo de produção, não a cada merge. Não repetir suíte já executada sobre o mesmo estado. Registrar ambiente, commit e resultado.
- **Contexto:** buscar antes de ler; ler trechos antes de arquivos inteiros; não reler documentos inalterados; não imprimir saídas longas (usar `tail`, `grep`, contagens). Sessão longa: compactar preservando objetivo, branch/HEAD, issues, pendências e agentes ativos; nova tarefa não relacionada: sessão limpa após registrar o estado.
- **Modelos:** o menor nível que resolva com segurança. Opus só para decisões complexas, riscos e conflitos difíceis; nada de Opus para resumir logs, localizar arquivos ou verificações mecânicas. Inventários, correções de Markdown, buscas extensas, testes e implementações que Antigravity, Codex ou OpenCode (quando autorizado) possam executar não consomem a cota Claude.
- **Cotas e balanceamento:** distribuir as tarefas entre provedores para nenhuma cota esgotar (Claude: Opus/Sonnet; OpenAI: Codex; Google: Antigravity). O Antigravity usa os modelos Gemini disponíveis (`agy models`), alternando entre famílias (ex.: Gemini 4 Hydrogen, Gemini 4 Argon, Gemini 3.8 Flash) e não usa modelos Claude, para não consumir a cota Claude. Quando a cota de um modelo acaba, trocar de modelo no mesmo provedor antes de redistribuir para outro. Segundo worker Claude só quando nenhum agente de outro provedor puder assumir.
- **Despacho verificado:** depois de cada despacho, confirmar com `orca orchestration worker-show` que o worker começou (`activity=working`); ao revisar entregas, conferir também os workers que ainda não reportaram (`worker-read`: cota esgotada, espera ou travamento) e agir na hora.

## Continuidade

Ao concluir um incremento: atualizar Issue + Status do Project + evidências (obrigatório em toda transição), revisar, testar, integrar e fazer push quando os critérios forem atendidos, atualizar o Project, encerrar a tarefa e limpar worktree e branches conforme [Encerramento de tarefas e limpeza](#encerramento-de-tarefas-e-limpeza) e delegar a próxima tarefa independente enquanto houver capacidade e autorização. Agente bloqueado ou no limite de uso: registrar e redistribuir. Não prometer execução futura sem mecanismo ativo; registrar o ponto de retomada na Issue.

**Delegação contínua:** a fila de trabalho são as Issues do Project. Quando um agente termina: receber resultado e evidências; encaminhar revisão/integração se preciso; escolher na fila a próxima tarefa prioritária, independente e autorizada; delegar já ao agente livre, sem esperar os demais; atualizar Issue e Status. Não delegar quando a tarefa depender de código não integrado, sobrepuser arquivos em andamento, exigir autorização ainda não dada ou o agente estiver sem cota; não criar tarefas artificiais.

## Retomada em outro computador ou nova sessão

O estado oficial não fica na máquina: está no Git (`main`, branches `feat/*`/`fix/*` publicadas) e no GitHub Projects/Issues. Estado do Orca (Runs, mensagens, worktrees) e memórias de agentes são locais e não são fonte de verdade.

1. Preparar o ambiente conforme [CONTINUAR-EM-OUTRO-PC.md](CONTINUAR-EM-OUTRO-PC.md).
2. Ler este documento e o [AGENTS.md](../AGENTS.md).
3. Conferir `main` e `origin/main`, branches publicadas e worktrees locais existentes (preservando alterações não commitadas).
4. Consultar no Project os itens Em andamento, Em revisão e Bloqueado e o último comentário de cada Issue (ponto de retomada).
5. Recriar worktrees e delegar a partir dessas Issues; registrar a retomada na Issue.

## Comunicação com o usuário

Relatórios curtos: Concluído, Validação, Em andamento, Bloqueios reais e Próximo incremento. Decisões sensíveis vêm com opções e recomendação.
