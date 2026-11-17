---
name: horus-maestro
description: Inicializa, retoma, sincroniza ou transfere a coordenação do projeto Hórus em uma sessão nova ou em outro computador, reconstruindo o estado real a partir do repositório, do Git e do GitHub Projects. Use quando o usuário pedir para iniciar, retomar, continuar, sincronizar ou transferir o contexto do Hórus.
---

# Hórus — inicialização do Maestro (Codex)

Procedimento canônico: `docs/RETOMADA-MAESTRO.md`, a partir da raiz do repositório. Esta skill não duplica aquele conteúdo.

A skill deve ser invocada explicitamente; sua presença não garante carregamento automático.

1. Leia o procedimento vigente em `docs/RETOMADA-MAESTRO.md`, seguindo a leitura progressiva da etapa A (governança obrigatória; ROADMAP só fase/estado, arquitetura só “Implementação atual”; máquina nova e documentos temáticos sob demanda), e execute as etapas A a D em **modo somente leitura**: sem reset, clean, merge, pull, stash ou push, sem instalar, configurar, alterar Issues ou o Project, e sem imprimir tokens.
2. Apresente o resumo da etapa E.
3. Retome somente o que `docs/AGENT-GOVERNANCE.md` autoriza para o seu papel, encerrando tarefas concluídas conforme a seção "Encerramento de tarefas e limpeza" dela.
   - Como **programador**: trabalhe na tarefa e nos arquivos atribuídos, faça commits na própria branch e entregue ao Maestro.
   - Como **Maestro**, só quando o usuário designar: coordenação completa conforme a governança.

Nunca, sem autorização específica do usuário: inferência real do Hermes, APIs ou serviços pagos, credenciais, publicação, envio de dados privados, Git destrutivo.
