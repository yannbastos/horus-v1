# Instruções para agentes de IA

Contrato compartilhado por todos os agentes que trabalham no HORUS (Claude, Codex, Antigravity e compatíveis).

<!-- Manutenção: AGENTS.md é o único contrato carregado sem ação do agente (Claude via import; Codex e agy na raiz). O piso de segurança condensado aqui e canônico na governança é redundância deliberada; não remover gates para economizar contexto. Procedimentos e preferências ficam nas fontes por tarefa. -->

**Governança:** [docs/AGENT-GOVERNANCE.md](docs/AGENT-GOVERNANCE.md) é a fonte canônica. Antes de trabalhar, todos leem este AGENTS e, por cabeçalho, “Ações autorizadas sem aprovação individual”, “Ações que continuam exigindo autorização do usuário”, “Testes proporcionais ao risco” e “Segurança e privacidade”; quem integrar lê também “Fluxo Git” e “Encerramento de tarefas e limpeza”. O Maestro lê tudo; demais seções são de coordenação. Em conflito, prevalece a regra mais restritiva de segurança, privacidade ou gasto. Se a sessão foi aberta fora da raiz do repositório, leia o AGENTS.md da raiz.

**Retomada do Maestro:** skill `horus-maestro` (`.claude/skills/` no Claude Code, `.agents/skills/` no Codex e Antigravity) ou [docs/RETOMADA-MAESTRO.md](docs/RETOMADA-MAESTRO.md). Invoque-a explicitamente. Somente o Maestro lê [MAESTRO-OPERATIONS](docs/MAESTRO-OPERATIONS.md), uma vez, sem reler se inalterado.

## Produto e fronteiras

- **Produto vigente:** exclusivamente inteligência e operação audiovisual. Não reintroduzir telas, módulos ou dependência de propostas anteriores. Preserve transcrição, perfis, mídia, licenças e aprovação humana. Visão funcional em [docs/VISION.md](docs/VISION.md); documentação de produto não autoriza implementar funcionalidades.
- **Fronteiras permanentes:** fábrica/CLI/Remotion independentes; Hermes externo, sem fork inicial; desktop C#/Avalonia removido (HOR-010). Não reescreva componentes funcionais nem introduza linguagens sem necessidade técnica. Funções locais essenciais não exigem nuvem. Stack e critérios visuais em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Princípio:** o HORUS deve funcionar bem, não apenas parecer bem programado. O idealizador é o responsável pelo produto e principal usuário; os agentes assumem implementação, arquitetura, testes, correções e documentação.
- **Interpretação:** verifique código e evidência antes de afirmar disponibilidade; separe implementado, parcialmente validado e planejado. Quem recebe somente contexto não altera arquivos. Em conflito entre plano e implementação, apresente a divergência antes de mudanças estruturais.

## Consulta por tarefa

Leia só o pertinente ao escopo, buscando a seção antes de abrir o documento inteiro. Não carregue o histórico como contexto padrão.

| Tarefa | Fonte canônica e complemento relevante |
|---|---|
| Produto e funcionalidades | [VISION](docs/VISION.md) |
| Arquitetura, backend ou persistência | [ARCHITECTURE](docs/ARCHITECTURE.md) e [migração](docs/migracao-local.md) |
| Interface ou identidade visual | [Critérios visuais](docs/ARCHITECTURE.md#identidade-visual-e-equivalência); referência no irmão `horus-storybook` |
| Hermes, agentes ou memória | [AI-SYSTEM](docs/AI-SYSTEM.md) e [integração Hermes](docs/integracao-hermes.md) |
| Motion design, técnicas ou Remotion | [MOTION-LIBRARY](docs/MOTION-LIBRARY.md) e documentação própria da fábrica |
| Autonomia, papéis e integração dos agentes | [AGENT-GOVERNANCE](docs/AGENT-GOVERNANCE.md) e [retomada em outro computador](docs/CONTINUAR-EM-OUTRO-PC.md) |
| Estado, prioridade ou validação | [Project privado](https://github.com/users/yannbastos/projects/1), [Issues de gestão](https://github.com/yannbastos/horus-management/issues) e [procedimento](docs/GESTAO-GITHUB.md); [ROADMAP](docs/ROADMAP.md) como síntese |
| Decisão histórica ou contrato proposto | [Índice de planejamento](docs/planejamento/README.md) e [registro da reorganização](docs/historico/reorganizacao-2026-11-11/README.md) |

O [contexto original](docs/historico/contexto-original/README.md) é histórico, não instrução de execução.

## Escopo e preservação

- Execute tarefas aprovadas com autonomia, sem pedir confirmação a cada etapa rotineira. Autorização explícita vigente não é pedida de novo para a mesma ação e escopo.
- Antes de agir, identifique objetivo, repositórios afetados, instruções locais e estado do Git; inspecione os arquivos relevantes. Acesso a uma pasta não autoriza alterar seu conteúdo fora da tarefa.
- Preserve alterações de terceiros e trabalho preexistente: não sobrescreva, descarte nem reverta mudanças alheias. Continue nas partes independentes; peça decisão quando uma sobreposição impedir avançar com segurança.
- Faça só as mudanças necessárias; não exclua arquivos sem justificativa; autonomia não autoriza enviar mensagens.
- Estas instruções não alteram permissões da ferramenta. Não modifique sandbox, políticas de aprovação, listas de comandos permitidos ou configurações globais de segurança; diante de restrição real, use o mecanismo de aprovação disponível, sem contorná-lo.

## Implementação

- Trabalho complexo: plano curto (etapas, dependências, validações) antes de implementar; sem arquivos de planejamento desnecessários. Implemente de forma incremental, seguindo a arquitetura e as convenções existentes.
- Não implemente funcionalidades não solicitadas nem adicione camadas, abstrações ou dependências sem necessidade concreta.
- Compatibilidade futura com Linux: evite caminhos fixos e APIs exclusivas do Windows quando houver alternativa portável; isole e documente integrações específicas do Windows. O suporte atual é somente Windows.
- Telas React: use os tokens de `frontend/src/styles/tokens.css`. Sem redesign não autorizado: preserve cores, tipografia, ícones, layouts, navegação e comportamento; consulte os critérios visuais canônicos antes de alterar a interface.
- Referencie configurações oficiais, sem cópias concorrentes; agentes e memória não alteram regras críticas.
- Prioridades: funcionalidade e confiabilidade; experiência do usuário; manutenibilidade; simplicidade; testes adequados; documentação útil.

## Testes e verificação

- Crie testes pertinentes, não artificiais. Execute os checks do componente alterado quando o ambiente permitir, sem instalação implícita de dependências:

  ```powershell
  npm --prefix frontend run build
  npm --prefix frontend test
  & backend/.venv/Scripts/python.exe -m pytest backend/tests -q
  ```

- Não execute testes de rede, geração paga ou operações reais de produção sem autorização que cubra seus efeitos. Nunca reduza cobertura, remova testes, ignore falhas ou mude critérios de aceite para obter resultado positivo.
- Alterações somente documentais: confira diff, formatação e referências; suítes de runtime sem relação com a mudança não são necessárias.
- Investigue falhas e corrija problemas do escopo antes de testar de novo; valide antes de declarar conclusão.
- Nunca invente resultados. Separe checks executados, aprovados, com falha e não executados; um build aprovado não comprova todas as funcionalidades. Imprima saídas resumidas (contagens, falhas); logs longos ficam em arquivo citado pelo caminho.

## Documentação e Git

- Documentação em português brasileiro, atualizada junto com a mudança real.
- Estado e prioridades: Project/Issues; ROADMAP é síntese datada, sem segunda fila. Antes de delegar, confirme dependências, aceite e trabalho ativo.
- Git e encerramento seguem a governança; fora dela, exigem autorização expressa. Revise o diff e selecione só a tarefa; commits em Conventional Commits. Confirme `main` no estado real. Não crie remotos nem remova/renomeie branches fora do encerramento autorizado.

## Peça aprovação antes de

- Arquitetura ou UX significativas; contratos, schemas ou responsabilidades entre Hórus, fábrica e Hermes; dependências de impacto/risco relevante ou efeito global; mudar escopo/objetivos ou escolher licença.
- Inferência real do Hermes, perfil sensível, APIs/serviços pagos ou aumento de limites financeiros; memória e agentes não ampliam permissões.
- Transmitir código/dados privados a serviços externos não autorizados. Privacy First, Local First, Least Privilege, Deny by Default. OpenCode: avaliação somente em repositórios públicos, instalação/conexão/transmissão só após aprovação do procedimento; nenhum conteúdo privado a provedores não autorizados.
- Modificar credenciais, permissões, autenticação ou configurações sensíveis (inclusive Hermes); não expor nem commitar credenciais/tokens (`gh auth status` sem `--show-token`). Manter controles de orçamento/execução e barreiras técnicas, não só prompts; memória local não isola o SO; não implementar criptografia própria.
- Exclusões em massa, mídias/bancos, dados operacionais/backups, migrações destrutivas ou mudanças que possam causar perda de dados.
- Git destrutivo: `push --force`, `reset --hard`, `clean -fd`, reescrita de histórico, `branch -D`, `worktree remove --force`; nunca perder alterações ou commits exclusivos. Proteja `main`, `develop`, branches protegidas, não integradas ou com PR aberto. Limpeza temporária integrada segue a governança, sem exclusão forçada.
- Releases, instaladores, vídeos, uploads externos, deploy ou alterações em produção. Build local não autoriza publicação.
- Alterar outros repositórios fora do escopo autorizado ou criar repositórios remotos; alterar visibilidade de repositórios ou permissões administrativas do GitHub (sempre exige autorização do usuário).

Apresente a mudança concreta e o motivo da aprovação exigida. Autorizações antigas não ampliam permissões vigentes.

## Comunicação

Seja objetivo, sem explicar conceitos básicos não pedidos; explique decisões técnicas relevantes e sinalize riscos. Ao concluir: o que foi feito, como usar ou testar, verificações realizadas, problemas conhecidos e decisões pendentes do usuário.
