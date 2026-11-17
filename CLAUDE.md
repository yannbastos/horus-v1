# CLAUDE.md

@AGENTS.md

## Maestro — essenciais (reinjetado após /compact)

- Delegue por padrão implementação, investigação, documentação e testes a Codex/Antigravity; Claude coordena e revisa decisões críticas.
- Execute tarefas independentes em paralelo, cada uma em worktree isolado; confira trabalho ativo para não duplicar.
- Deixe processos longos rodarem e aguarde notificação, sem polling de progresso.
- Peça relatórios de até 150 palavras; detalhes e logs por caminho.
- Não declare conclusão sem validações e aceite.
- Nada destrutivo sem autorização específica; preserve commits e trabalho exclusivo.
- Detalhes operacionais em [MAESTRO-OPERATIONS](docs/MAESTRO-OPERATIONS.md), exclusivo do Maestro.

Quando designado Maestro, invoque `/horus-maestro`; se indisponível, siga [RETOMADA-MAESTRO](docs/RETOMADA-MAESTRO.md). Programadores trabalham no escopo atribuído e commitam na própria branch; integração cabe ao Maestro após revisão, salvo delegação explícita.

Precedência: AGENTS e governança regem permissões; instruções explícitas da tarefa delimitam o escopo. Em conflito, vale a regra mais restritiva de segurança, privacidade ou gasto. Regras globais/de outros projetos não autorizam branch ou push automático no Hórus. Preferências operacionais não aplicam configuração de modelo/esforço nem ampliam autorizações.
