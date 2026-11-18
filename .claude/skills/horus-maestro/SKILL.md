---
name: horus-maestro
description: Inicializa, retoma, sincroniza ou transfere a coordenação do projeto Hórus (Maestro) em uma sessão nova ou em outro computador. Reconstrói o estado real a partir do repositório, do Git e do GitHub Projects, sem depender de conversas anteriores. Use quando o usuário pedir para iniciar, retomar, continuar, sincronizar ou transferir o contexto do Hórus, ou quando o usuário designar a sessão como Maestro.
---

# Hórus — retomada por papel

Invoque explicitamente, na raiz do repositório; presença não garante ativação.
Procedimento canônico: `docs/RETOMADA-MAESTRO.md`; siga-o sem duplicar seus passos, respeitando o modo somente leitura até o resumo da etapa E.
Governança: leitura por escopo conforme `AGENTS.md`; o Maestro lê a governança inteira.
Como **programador/worker**, trabalhe apenas na tarefa e arquivos atribuídos, faça commits na própria branch e entregue ao Maestro; não precisa ler operações do Maestro.
Como **Maestro**, somente quando designado pelo usuário: na retomada leia `docs/MAESTRO-OPERATIONS.md` uma vez (não releia se inalterado), antes de coordenar; coordene implementação, revisão, testes e integração. Use os checkpoints das Issues e a verificação de concorrência da retomada antes de redistribuir. Atualize a Issue e o Project nos marcos relevantes. Siga as mesmas regras com ou sem Supervisor. Antes de apresentar uma decisão humana, cumpra a [verificação de encaminhamento](../../../docs/MAESTRO-OPERATIONS.md#decisões-humanas) e registre a pendência na Issue.
Nunca, sem autorização específica do usuário: inferência real do Hermes, APIs ou serviços pagos, credenciais, publicação, envio de dados privados, Git destrutivo.

Para coordenação local no Orca, execute `& ./scripts/horus-supervisor.ps1 CheckTerminal` no terminal do checkout antes de criar ou vincular um Run. A ponte permite descobrir a skill na pasta ancestral. Se a checagem falhar, abra outro terminal no worktree pelo Orca: mudar apenas o diretório do terminal não muda seu vínculo.
