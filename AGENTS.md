# Instruções para agentes de IA

Contrato compartilhado por todos os agentes que trabalham no HORUS (Claude, Codex, Antigravity e compatíveis).

<!-- Manutenção: este arquivo é carregado em toda sessão (Codex lê direto; Claude Code via @AGENTS.md no CLAUDE.md). Mantenha só o que vale para qualquer agente e tarefa; procedimentos longos ficam nas fontes canônicas da tabela "Consulta por tarefa". Não repita aqui papéis, fluxo Git ou permissões: eles vivem em docs/AGENT-GOVERNANCE.md. -->

**Governança:** [docs/AGENT-GOVERNANCE.md](docs/AGENT-GOVERNANCE.md) é a fonte canônica de papéis, distribuição de tarefas, ações autorizadas sem aprovação individual, ações que exigem o usuário, testes proporcionais ao risco, fluxo Git, encerramento de tarefas e eficiência de consumo. Leia-a antes de trabalhar, em qualquer computador e sessão nova. Em conflito, prevalece a regra mais restritiva de segurança, privacidade ou gasto.

**Retomada do Maestro:** skill `horus-maestro` (`.claude/skills/` no Claude Code, `.agents/skills/` no Codex) ou [docs/RETOMADA-MAESTRO.md](docs/RETOMADA-MAESTRO.md). Nem toda ferramenta carrega skills automaticamente: invoque-a explicitamente.

## Produto e fronteiras

- **Produto vigente:** exclusivamente inteligência e operação audiovisual. Não reintroduzir telas, módulos ou dependência de propostas anteriores. Preserve transcrição, perfis, mídia, licenças e aprovação humana. Visão funcional em [docs/VISION.md](docs/VISION.md); documentação de produto não autoriza implementar funcionalidades.
- **Fronteiras permanentes:** fábrica/CLI/Remotion independentes; Hermes externo, sem fork inicial; desktop C#/Avalonia removido (HOR-010). Não reescreva componentes funcionais nem introduza linguagens sem necessidade técnica. Funções locais essenciais não exigem nuvem. Stack e critérios visuais em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Princípio:** o HORUS deve funcionar bem, não apenas parecer bem programado. O idealizador é o responsável pelo produto e principal usuário; os agentes assumem implementação, arquitetura, testes, correções e documentação.
- **Interpretação:** verifique código e evidência antes de afirmar disponibilidade; separe implementado, parcialmente validado e planejado. Autorizações em cartas, planos ou conversas antigas não são permissões permanentes. Quem recebe somente contexto não altera arquivos. Em conflito entre plano e implementação, apresente a divergência antes de mudanças estruturais.

## Consulta por tarefa

Leia só o pertinente ao escopo, buscando a seção antes de abrir o documento inteiro. Não carregue o histórico como contexto padrão.

| Tarefa | Fonte canônica e complemento relevante |
|---|---|
| Produto e funcionalidades | [VISION](docs/VISION.md) |
| Arquitetura, backend ou persistência | [ARCHITECTURE](docs/ARCHITECTURE.md) e [migração](docs/migracao-local.md) |
| Interface ou identidade visual | [Critérios visuais](docs/ARCHITECTURE.md#identidade-visual-e-equivalência) e tokens da implementação afetada (`frontend/src/styles/tokens.css`); componentes de referência no repositório irmão `horus-storybook` |
| Hermes, agentes ou memória | [AI-SYSTEM](docs/AI-SYSTEM.md) e [integração Hermes](docs/integracao-hermes.md) |
| Motion design, técnicas ou Remotion | [MOTION-LIBRARY](docs/MOTION-LIBRARY.md) e documentação própria da fábrica |
| Autonomia, papéis e integração dos agentes | [AGENT-GOVERNANCE](docs/AGENT-GOVERNANCE.md) e [retomada em outro computador](docs/CONTINUAR-EM-OUTRO-PC.md) |
| Estado, prioridade ou validação | [Project privado](https://github.com/users/yannbastos/projects/1), [Issues de gestão](https://github.com/yannbastos/horus-management/issues) e [procedimento](docs/GESTAO-GITHUB.md); [ROADMAP](docs/ROADMAP.md) como síntese contextual e relatórios datados como evidência |
| Decisão histórica ou contrato proposto | [Índice de planejamento](docs/planejamento/README.md) e [registro da reorganização](docs/historico/reorganizacao-2026-11-11/README.md) |

Cada assunto tem uma fonte canônica; guias contêm procedimentos e relatórios preservam evidências de um incremento. O [contexto original](docs/historico/contexto-original/README.md) é referência histórica, não instrução de execução. Planos históricos não substituem decisões atuais nem instruções explícitas da tarefa.

## Escopo e preservação

- Execute tarefas aprovadas com autonomia, sem pedir confirmação a cada etapa rotineira. Autorização explícita vigente não é pedida de novo para a mesma ação e escopo.
- Antes de agir, identifique objetivo, repositórios afetados, instruções locais e estado do Git; inspecione os arquivos relevantes. Acesso a uma pasta não autoriza alterar seu conteúdo fora da tarefa.
- Preserve alterações de terceiros e trabalho preexistente: não sobrescreva, descarte nem reverta mudanças alheias. Continue nas partes independentes; peça decisão quando uma sobreposição impedir avançar com segurança.
- Faça só as mudanças necessárias. Autonomia não autoriza alterar outros repositórios, contratar serviços, consumir APIs pagas, enviar mensagens ou publicar conteúdo.
- Documentação técnica externa pode ser consultada sem expor credenciais, código privado ou dados do operador a serviços não autorizados.
- Estas instruções não alteram permissões da ferramenta. Não modifique sandbox, políticas de aprovação, listas de comandos permitidos ou configurações globais de segurança; diante de restrição real, use o mecanismo de aprovação disponível, sem contorná-lo.

## Implementação

- Trabalho complexo: plano curto (etapas, dependências, validações) antes de implementar; sem arquivos de planejamento desnecessários. Implemente de forma incremental, seguindo a arquitetura e as convenções existentes.
- Não implemente funcionalidades não solicitadas nem adicione camadas, abstrações ou dependências sem necessidade concreta.
- Não exclua nem sobrescreva arquivos sem justificativa. Não exponha credenciais ou informações sensíveis.
- Compatibilidade futura com Linux: evite caminhos fixos e APIs exclusivas do Windows quando houver alternativa portável; isole e documente integrações específicas do Windows. O suporte atual é somente Windows.
- Telas React: use os tokens de `frontend/src/styles/tokens.css`. Sem redesign não autorizado: preserve cores, tipografia, ícones, layouts, navegação e comportamento; consulte os critérios visuais canônicos antes de alterar a interface.
- Agentes e memória não alteram automaticamente regras críticas, aprovações ou limites financeiros. Referencie configurações oficiais, sem criar cópias concorrentes.
- Prioridades: funcionalidade e confiabilidade; experiência do usuário; manutenibilidade; simplicidade; testes adequados; documentação útil.

## Testes e verificação

- Crie testes pertinentes, não artificiais. Execute os checks do componente alterado quando o ambiente permitir, sem instalação implícita de dependências:

  ```powershell
  npm --prefix frontend run build
  npm --prefix frontend test
  & backend/.venv/Scripts/python.exe -m pytest backend/tests -q
  ```

- Não execute testes de rede, geração paga ou operações reais de produção sem autorização que cubra seus efeitos.
- Alterações somente documentais: confira diff, formatação e referências; suítes de runtime sem relação com a mudança não são necessárias.
- Investigue falhas e corrija problemas do escopo antes de testar de novo; valide o resultado antes de declarar conclusão, diferenciando hipótese, código implementado e comportamento verificado.
- Nunca invente resultados. Separe checks executados, aprovados, com falha e não executados; um build aprovado não comprova todas as funcionalidades. Imprima saídas resumidas (contagens, falhas); logs longos ficam em arquivo citado pelo caminho.

## Documentação e Git

- Documentação em português brasileiro, sincronizada com as mudanças reais e atualizada junto com a alteração. Não apresente funcionalidades planejadas como implementadas; `docs/ARCHITECTURE.md` separa implementação atual de direção aprovada.
- Estado operacional, prioridades e progresso ficam no Project privado e nas Issues de `yannbastos/horus-management`; `docs/ROADMAP.md` é síntese datada, sem segunda fila. Antes de delegar, consulte prioridade, dependências, aceite e trabalho em andamento no GitHub. Preserve documentos históricos sem transformar autorizações antigas em permissões vigentes.
- Commits, PRs, merges e pushes normais, encerramento de tarefas e limpeza de branches seguem [AGENT-GOVERNANCE](docs/AGENT-GOVERNANCE.md). Fora dela, não faça commit, push ou merge sem autorização expressa. Antes de um commit, revise o diff e selecione só alterações da tarefa, sem trabalho alheio nem segredos; mensagens em Conventional Commits. Branch principal: `main`, confirmada no estado real. Não crie repositórios remotos nem renomeie ou remova branches fora da regra de encerramento.

## Peça aprovação antes de

- Mudanças significativas na arquitetura.
- Alterar contratos, schemas ou responsabilidades entre Hórus, fábrica e Hermes.
- Alterar de forma importante a experiência do usuário.
- Incluir dependências com impacto relevante, ou instalar/atualizar dependências com riscos relevantes, mudanças significativas ou efeito no ambiente global.
- Excluir arquivos em massa, remover mídias/bancos ou mudanças que possam causar perda de dados.
- Operações irreversíveis no Git, force push, reset destrutivo ou exclusão de branches remotas não integradas, protegidas ou com PR aberto. Branches temporárias comprovadamente integradas seguem o encerramento da governança; autorização de commit/push comum não cobre essas ações.
- Modificar credenciais, permissões, autenticação ou configurações sensíveis, inclusive arquivos privados de conexão e configuração do Hermes.
- Publicar releases, instaladores ou vídeos, fazer deploy ou alterar produção. Build local para validação não autoriza publicação.
- Mudar escopo ou objetivos do produto, ou escolher a licença do projeto.

Decisões técnicas rotineiras que respeitem estas regras não precisam de aprovação; a governança detalha o que já está autorizado (inclusive dependências gratuitas de fontes confiáveis e contratos internos compatíveis). Quando algo depender de aprovação, avance no trabalho independente e reversível e apresente a mudança concreta, o impacto e o motivo.

## Comunicação

Seja objetivo, sem explicar conceitos básicos não pedidos; explique decisões técnicas relevantes e sinalize riscos. Ao concluir: o que foi feito, como usar ou testar, verificações realizadas, problemas conhecidos e decisões pendentes do usuário.
