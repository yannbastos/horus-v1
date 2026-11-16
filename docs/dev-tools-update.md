# Atualização portátil de ferramentas de desenvolvimento

Os scripts rodam no Windows PowerShell 5.1, sem administrador. Não instalam ferramentas ausentes e não alteram PATH, credenciais, modelos, memória nem configurações. São independentes do runtime do Hórus. Skills equivalentes ficam em `.agents/skills/dev-tools-update/` e `.claude/skills/dev-tools-update/`.

Na raiz do checkout:

```powershell
powershell -NoProfile -File scripts/update-dev-tools.ps1 -Check
powershell -NoProfile -File scripts/update-dev-tools.ps1 -DryRun -All
powershell -NoProfile -File scripts/update-dev-tools.ps1 -All -NonInteractive
powershell -NoProfile -File scripts/update-dev-tools.ps1 -Tool codex
```

Sem parâmetros, apenas consulta. `-Check` e `-DryRun` prevalecem sobre seleções de atualização. `-Tool` aceita claude, codex, antigravity e hermes, inclusive uma lista PowerShell. `-All` autoriza tentar todas as atualizações elegíveis; não remove bloqueios. `-NonInteractive` evita qualquer fluxo de confirmação do script, mas não autoriza atualizar sozinho. O script não abre sessões de inferência.

Cada ferramenta retorna um objeto com todos os caminhos encontrados no PATH, apps Antigravity no registro, origem, versões anterior/estável/final, método, fonte, status, motivo e recuperação manual. O log resumido sai no console; para guardar o inventário, canalize os objetos para `ConvertTo-Json -Depth 6` e salve em arquivo local. Não publique inventários com caminhos pessoais. Erro em uma ferramenta não interrompe as demais.

## Fontes e métodos oficiais

Fontes consultadas em 16/11/2026; a ausência de comprovação de um método impede sua execução automática.

| Ferramenta | Versão estável consultada | Método oficial e comportamento do script |
|---|---|---|
| Claude Code | `dist-tags.stable` de `@anthropic-ai/claude-code` no registro npm oficial | [Setup oficial](https://code.claude.com/docs/en/setup): npm global usa `npm install --global @anthropic-ai/claude-code@VERSAO`, fixando a versão estável. Nativa oferece `claude update`, mas depende do canal em settings: fica manual para não alterar configuração nem usar latest por engano. |
| Codex CLI | [Release pública mais recente](https://api.github.com/repos/openai/codex/releases/latest), recusando draft/prerelease | [Repositório oficial](https://github.com/openai/codex): npm global usa `npm install --global @openai/codex@VERSAO`. A instalação nativa que oferece `codex update` fica manual quando não permite fixar explicitamente versão/canal; o comando foi confirmado no help local, sem execução. |
| Antigravity CLI (`agy`) | Manifesto público `windows_amd64.json` ou `windows_arm64.json` do serviço de distribuição indicado no instalador oficial; exige versão sem prerelease e URL do bucket oficial | [Instalação oficial](https://antigravity.google/docs/getting-started?tab=cli), [referência](https://antigravity.google/docs/cli/reference/). O instalador foi apenas lido para identificar o manifesto; nunca executado. O `agy update` confirmado no help local fica manual porque não oferece seleção explícita de canal/versão. O app é inventariado separadamente pelo registro; versão do CLI não determina versão do app. Atualização do app é pela interface/instalador oficial, manual. |
| Hermes Agent | [Release oficial](https://api.github.com/repos/NousResearch/hermes-agent/releases/latest), sem draft/prerelease | [Atualização oficial](https://hermes-agent.nousresearch.com/docs/getting-started/updating): `hermes update --channel stable --no-gateway-restart` para checkout oficial gerenciado. O script apenas relata, pois o próprio atualizador pode migrar configuração/estado; essa intervenção não cabe no compromisso de preservar configurações e memória. MSIX/Store/Docker/Nix usam seu distribuidor original, manualmente. |

Não confundir `latest` do Claude com o canal `stable` defasado de segurança. Nunca fazer downgrade automático. Nenhum script remoto é executado. A consulta ao manifesto agy acompanha a distribuição pública normal do instalador; se o fornecedor mudar schema, bucket ou semântica, a revisão manual continua obrigatória. Não existe promessa de atualização automática universal.

## Origem, conflitos e limites

O inventário usa `Get-Command -All`, manifestos npm globais com nome e prefixo correspondentes, caminhos convencionais de instalação por usuário, e registro de apps Antigravity. Distingue indícios de pip/venv, pipx, uv tool, winget/MSIX e scoop; esses caminhos são evidências, não certificação da distribuição. Instalação fora das convenções fica desconhecida/manual. Gerenciadores alternativos não são substituídos por npm. Shims adjacentes `.cmd`/`.ps1` do mesmo diretório aparecem no relatório sem conflito; origens/diretórios diferentes ou aliases bloqueiam a atualização.

As atualizações automáticas desta versão cobrem **npm global comprovado** para Claude/Codex, com versão exata e `--engine-strict`. A incompatibilidade de Node/engines interrompe a atualização, sem instalar Node. Atualizadores nativos e demais gerenciadores são relatados para intervenção manual. Instalações fora do perfil, em Program Files/ProgramData/Windows, apps por máquina e políticas corporativas conhecidas bloqueiam a atualização. A detecção de políticas é conservadora e não cobre toda solução de gestão de TI: diante de dúvida, consulte TI, sem contornar políticas.

`Get-Process` e `Win32_Process` inspecionam nomes, executáveis e linhas de comando em memória; o relatório não inclui linhas de comando que possam conter dados sensíveis. Hosts Python/Node cuja linha não seja acessível também bloqueiam. Falha no inventário bloqueia. Há segunda verificação imediatamente antes da mutação; não há trava de exclusão mútua com lançadores externos, portanto encerre as sessões antes de solicitar atualização. O script nunca encerra processos.

Antes da mutação, registra versão e comando. Depois, resolve novamente o executável e confirma inicialização por `--version` e igualdade com a versão solicitada. Isso verifica inicialização básica, não autenticação nem inferência. Falha deixa recuperação manual pelo distribuidor original; não há rollback garantido e não se restauram configurações/dados automaticamente.

## Agendador opcional

```powershell
powershell -NoProfile -File scripts/register-dev-tools-update-task.ps1 -DryRun
# Somente depois de autorização específica do usuário:
powershell -NoProfile -File scripts/register-dev-tools-update-task.ps1 -Register
powershell -NoProfile -File scripts/register-dev-tools-update-task.ps1 -Unregister
```

O padrão é DryRun. Plano por usuário, logon interativo, sem elevação, senha ou alteração de ExecutionPolicy; não sobrescreve tarefa existente. A política é **verificar ao logon; atualizar só sem sessões ativas e com autorização explícita**. Para não transformar o logon em autorização permanente, a tarefa executa somente `-Check`; a atualização fica sob comando explícito. Não foi registrada tarefa real nesta entrega. A tarefa depende de o checkout permanecer no caminho registrado; após movê-lo, revise/remova e registre novamente com autorização. Seu histórico confirma execução, mas não guarda automaticamente os objetos do inventário.

## Validação

```powershell
powershell -NoProfile -File scripts/test-update-dev-tools.ps1
```

Testes próprios com mocks, sem dependências/Pester, rede ou atualização real. Cobrem instalada, ausente, já atualizada, sem downgrade, atualização disponível, falha de rede, sessões, TI, conflitos, gerenciadores alternativos, versões incompatíveis/prerelease, DryRun, falha durante atualização, confirmação final, seleção e registro simulado. Execute também `-Check` real para validar descoberta no computador de destino. Linux/macOS recebem diagnóstico de incompatibilidade; não há atualizador multiplataforma nesta entrega.
