---
name: dev-tools-update
description: Verifica instalações e atualizações estáveis de Claude Code, Codex CLI, Antigravity e Hermes, e executa somente as atualizações de desenvolvimento autorizadas pelo usuário.
---

# Atualização das ferramentas de desenvolvimento

Localize a raiz do checkout por `git rev-parse --show-toplevel` ou subindo a partir desta skill até encontrar `scripts/update-dev-tools.ps1`. Todos os caminhos são relativos ao checkout; não dependa do perfil ou do computador de origem.

1. Detecte o SO. No Windows, use Windows PowerShell 5.1 e execute, a partir da raiz, `powershell -NoProfile -File scripts/update-dev-tools.ps1 -Check`. Em Linux/macOS, informe que o script Windows não é executável nesse SO e consulte os métodos oficiais de `docs/dev-tools-update.md`; não invente uma instalação alternativa.
2. Leia os objetos retornados: caminhos e conflitos, origem, versão, última estável, método e fonte. Sessão ativa, políticas corporativas, origem/canal não comprovados e versões incompatíveis exigem intervenção manual. Ausente não autoriza instalação.
3. Se a conversa já autoriza as atualizações indicadas, execute `-Tool <nome>` ou `-All`, opcionalmente com `-NonInteractive`. Caso contrário, apresente o resultado e obtenha autorização para o conjunto concreto. `-DryRun -All` simula; `-NonInteractive` sozinho apenas consulta. Não remova gates para forçar uma atualização.
4. Resuma ferramentas atualizadas, puladas, falhas isoladas e intervenções manuais com a fonte oficial. Confirme a versão final quando houver atualização; em falha, registre a recuperação manual sem prometer rollback.

Nunca encerre processos, altere PATH, canais/configurações, credenciais, modelos ou memória. Não rode beta/nightly, instaladores desconhecidos ou scripts remotos. O agendador `scripts/register-dev-tools-update-task.ps1` usa DryRun por padrão: registrar/remover a tarefa exige autorização específica; uma autorização de atualização não cobre registro. A tarefa de logon somente verifica e não constitui autorização para atualizar.
