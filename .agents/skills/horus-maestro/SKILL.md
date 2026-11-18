---
name: horus-maestro
description: Inicializa, retoma, sincroniza ou transfere a coordenação do projeto Hórus em uma sessão nova ou em outro computador, reconstruindo o estado real a partir do repositório, do Git e do GitHub Projects. Use quando o usuário pedir para iniciar, retomar, continuar, sincronizar ou transferir o contexto do Hórus.
---

# Hórus — retomada por papel

Invoque explicitamente, na raiz do repositório; presença não garante ativação.
Procedimento canônico: `docs/RETOMADA-MAESTRO.md`; siga-o sem duplicar seus passos, respeitando o modo somente leitura até o resumo da etapa E.
Governança: leitura por escopo conforme `AGENTS.md`; o Maestro lê a governança inteira.
Como **programador/worker**, trabalhe apenas na tarefa e arquivos atribuídos, faça commits na própria branch e entregue ao Maestro; não precisa ler operações do Maestro.
Como **Maestro**, somente quando designado pelo usuário: na retomada leia `docs/MAESTRO-OPERATIONS.md` uma vez (não releia se inalterado), antes de coordenar; opere conforme a governança, inclusive encerramento. Antes de criar ou vincular um Run no Orca, execute `& ./scripts/horus-supervisor.ps1 CheckTerminal` no checkout. Para decisões humanas, aplique a verificação em `docs/MAESTRO-OPERATIONS.md` e recupere o estado registrado na Issue.
Nunca, sem autorização específica do usuário: inferência real do Hermes, APIs ou serviços pagos, credenciais, publicação, envio de dados privados, Git destrutivo.
