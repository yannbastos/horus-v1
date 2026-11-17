# CLAUDE.md

@AGENTS.md

<!-- Manutenção: o import acima carrega o contrato compartilhado uma única vez (o Claude Code não relê um AGENTS.md já importado). Abaixo, só o que é específico do Claude Code; papéis, permissões, fluxo Git e eficiência vivem em docs/AGENT-GOVERNANCE.md e não são repetidos aqui. -->

## Claude Code no Hórus

- **Papéis:** Maestro ou Claude Sonnet programador, conforme a seção Papéis de `docs/AGENT-GOVERNANCE.md` (modelo do Maestro definido pelo usuário).
- **Início de sessão do Maestro:** invoque a skill `/horus-maestro`; se ela não carregar, siga `docs/RETOMADA-MAESTRO.md`. Somente leitura até o resumo do estado; depois, opere com a autonomia da governança, sem pedir confirmação para ações já autorizadas.
- **Delegação:** pelo Orca, com as skills `orca-cli` e `orchestration`. A tarefa delegada cita a governança em vez de repetir regras e segue as seções "Distribuição automática" e "Eficiência de consumo" (relatório curto, despacho verificado, cotas).
- **Contexto do Maestro:** subagentes do Claude Code (Explore, general-purpose) servem para buscas amplas cujo resultado é só a conclusão; implementação vai para os programadores via Orca. Não reler documentos já lidos e inalterados na sessão.
- **Claude Sonnet programador:** trabalha nos arquivos atribuídos pela tarefa e faz commits na própria branch; a integração na `main` é do Maestro após revisão.

## Precedência

Commits, merges e pushes normais seguem a governança; ações sensíveis continuam exigindo o usuário. Instruções globais do usuário (`~/.claude/CLAUDE.md`) ou de outros projetos, como regras de branch por tarefa e push automático após cada commit, não se aplicam ao Hórus e não constituem autorização. As preferências de comunicação do usuário continuam valendo. Em conflito, prevalecem `AGENTS.md`, a governança e as instruções explícitas da tarefa.
