# Continuar o desenvolvimento em outro computador (Windows)

Guia para clonar o Hórus em uma máquina nova e retomar o trabalho sem perder estado. Revisado em 2026-11-13. Nenhuma credencial, banco operacional ou backup acompanha os repositórios: tudo que é secreto é recriado localmente.

**Leitura obrigatória antes de retomar:** [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md) (política de autonomia, papéis, integração e restrições) e [AGENTS.md](../AGENTS.md). A política é versionada no repositório e não depende desta máquina; o roteiro de retomada da coordenação está na seção 15.

## 1. Repositórios

Crie uma pasta de trabalho (por exemplo `C:\Projetos\HORUS VIDEOMAKER\`) e clone **lado a lado** — o backend encontra a fábrica automaticamente quando ela é irmã do Hórus:

```powershell
gh auth login                      # conta do idealizador; escopos repo, project, workflow
gh repo clone yannbastos/horus "HORUS/HORUS App"
gh repo clone yannbastos/fabrica-canais
gh repo clone yannbastos/horus-storybook "HORUS/HORUS Storybook"   # catálogo visual (opcional)
gh repo clone yannbastos/estudos-videos-claude                       # referências (opcional)
gh repo clone yannbastos/horus-management                            # PRIVADO: registro da gestão (opcional)
```

| Repositório | Visibilidade | Conteúdo |
|---|---|---|
| `yannbastos/horus` | público | Código, testes, contratos e documentação técnica |
| `yannbastos/fabrica-canais` | — | Fábrica audiovisual independente (Python + Remotion) |
| `yannbastos/horus-management` | **privado** | Issues de gestão e registro da migração da planilha |

## 2. Branch recomendada

Continue a partir de **`main`**. As entregas integradas estão em `main`; branches `feat/*` publicadas e ainda não contidas em `main` são trabalho em andamento — confira no Project antes de retomá-las. Para trabalho novo, crie `feat/hor-XXX-descricao` a partir de `main`, conforme [GESTAO-GITHUB.md](GESTAO-GITHUB.md).

## 3. Ferramentas e versões

| Ferramenta | Versão | Uso |
|---|---|---|
| Git | 2.4x+ | — |
| GitHub CLI (`gh`) | 2.10x+ | Repositórios, Issues e Project |
| Python | **3.12** (exigido: `>=3.12,<3.13`) | Backend. O `uv` instala a versão correta se a do sistema for outra |
| uv | 0.12+ | Ambiente e lock do backend (`backend/uv.lock`) |
| Node.js | 24 LTS (compatível com Vite 8) | Frontend, Playwright e Remotion |
| Rust | stable (1.9x) + **MSVC Build Tools** com C++/Windows SDK + **WebView2** | Somente para o desktop Tauri |
| Microsoft Edge | atual | Os testes Playwright usam `channel: 'msedge'` |
| FFmpeg / ffprobe | opcional | Mesclar vídeo+áudio no download e na fábrica; a transcrição usa PyAV sem FFmpeg |

Instalação sem administrador: prefira instaladores por usuário (`winget --scope user`) ou zips portáteis em `%LOCALAPPDATA%\Programs` adicionados ao PATH do usuário.

## 4. Dependências

Na raiz do Hórus (`HORUS App`):

```powershell
uv sync --project backend --frozen --extra test                 # mínimo para desenvolver e testar
# opcionais: --extra transcription (Whisper/yt-dlp) --extra bundle (PyInstaller) --extra measure
npm ci --prefix frontend --ignore-scripts --userconfig frontend/.npmrc
npx --prefix frontend playwright install msedge                 # se o Edge não estiver disponível para o Playwright
```

`--frozen`/`npm ci` respeitam os locks versionados; não atualize dependências durante a retomada.

## 5. Backend

```powershell
$env:HORUS_API_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
& backend/.venv/Scripts/python.exe -m horus_backend --port 8765
```

Dados locais ficam em `%LOCALAPPDATA%\Horus\workspace\` (banco `horus.sqlite3`, Radar em `radar\`, conexão Hermes em `integrations\hermes.json`). Uma máquina nova começa **vazia**: não copie bancos ou backups da máquina antiga para o Git.

Variáveis de operador relevantes (todas opcionais; padrões seguros):

| Variável | Padrão | Efeito |
|---|---|---|
| `HORUS_WORKFLOW_EXECUTOR` | `simulated` | Executor de `POST /v1/workflows` |
| `HORUS_RADAR_EXECUTOR` | `hermes_simulated` | Workflows do Radar usam o Hermes simulado em processo |
| `HORUS_MAX_TOKENS_PER_RUN` / `_PER_DAY` / `HORUS_MAX_RUNS_PER_DAY` | 16384 / 32768 / 2 | Limites aprovados; valor inválido bloqueia execução real |
| `HORUS_BUDGET_TOTAL_USD` / `HORUS_BUDGET_DAILY_USD` | 5 / 1 | Orçamento estimado |
| `HORUS_PRICE_INPUT_USD_PER_MTOK` / `HORUS_PRICE_OUTPUT_USD_PER_MTOK` | — | **Obrigatórios para execução real**; ausentes ⇒ `pricing_missing` |
| `HORUS_BACKUP_RETENTION_DAYS` | — | Mantenha **sem valor** (limpeza de backups só em simulação) |
| `FABRICA_HOME` | descoberta automática | Caminho da fábrica se não for pasta irmã |

## 6. Frontend (navegador)

```powershell
npm --prefix frontend run dev        # http://127.0.0.1:1420
```

Em **Ajustes → Geral**, conecte `http://127.0.0.1:8765` com o token do passo 5. **Ajustes → Privacidade e Segurança** mostra o estado real do Hermes, limites e retenção.

## 7. Desktop (Tauri)

Com Rust, MSVC Build Tools e WebView2 instalados:

```powershell
npm --prefix frontend run desktop:dev
npm --prefix frontend run desktop:build     # gera backend empacotado (PyInstaller) e instalador NSIS
```

O instalador não inclui modelos Whisper, fábrica, Hermes, FFmpeg, Node ou Remotion.

## 8. Testes

```powershell
& backend/.venv/Scripts/python.exe -m pytest backend/tests -q
& backend/.venv/Scripts/python.exe -m pytest contracts/hermes/tests -q -p no:cacheprovider
npm --prefix frontend run build
npm --prefix frontend test           # confirme antes que nada escuta na porta 1420
```

Referência em `main` (2026-11-12): backend 455 aprovados + 1 ignorado (teste de symlink que exige privilégio no Windows), contrato 54, Playwright 84, build aprovado. Nenhum teste usa rede externa ou o banco operacional.

## 9. Fábrica audiovisual (`fabrica-canais`)

Componentes externos **não incluídos** no repositório do Hórus:

- Python 3.12 + `uv` (projeto em `scripts/`), Node.js/npm para `remotion/`.
- Serviços com conta própria (chaves em `.env` da fábrica, criado a partir de `.env.example`; **nunca versionar**): ElevenLabs (voz, gera custo), YouTube Data API, Pexels, Pixabay.
- Whisper/stable-ts para conferência e alinhamento; FFmpeg recomendado.
- `setup.ps1` instala/prepara ferramentas e vínculos de skills — **revise antes de executar**.

```powershell
uv sync --project ..\..\fabrica-canais\scripts --frozen --no-default-groups   # suficiente para a consulta de perfil pelo Hórus
```

## 10. Orca e agentes

1. Instale o Orca e adicione os repositórios pela interface (ou `orca repo add`).
2. Faça login nas CLIs dos agentes com as contas do idealizador: Claude Code (`claude`), Codex (`codex`) e Antigravity (`agy`).
3. Modelos validados (benchmark de 2026-11-12):
   - Codex: `gpt-6.1-sol` com esforço `high` para backend/segurança; `gpt-6-astra` só para o mais difícil.
   - Claude: Sonnet 5.5 (`--agent claude --model claude-sonnet-5-5 --effort high`) para implementação; Opus apenas para revisões críticas.
   - Antigravity: `gemini-4-argon-medium` para UI complexa (o Antigravity não usa modelos Claude: ver governança), `gemini-3.8-flash-low` para tarefas simples. **Evite Gemini 3.8 Flash High** (o mais lento no benchmark).
4. Siga [AGENT-GOVERNANCE.md](AGENT-GOVERNANCE.md), [AGENTS.md](../AGENTS.md) e [GESTAO-GITHUB.md](GESTAO-GITHUB.md): worktree por agente e arquivos reservados por tarefa. Programadores fazem commits nas próprias branches; o Maestro revisa, integra na `main` e faz push normal. Ações sensíveis (APIs pagas, inferência real, publicação, Git destrutivo, credenciais) continuam exigindo o usuário.

## 11. Hermes com segurança

A máquina nova **não** tem perfil `horus`. Recrie-o e endureça-o antes de qualquer uso:

1. Instale o Hermes CLI (padrão `%LOCALAPPDATA%\hermes`).
2. `./scripts/Prepare-Hermes.ps1` — cria o perfil isolado e a chave local (exige autorização, ver [integração Hermes](integracao-hermes.md)).
3. Endureça com dry-run e aplicação, conforme [segurança do Hermes](seguranca-hermes.md):
   `scripts/harden_hermes_profile.py --profile <perfil horus> --allow-real --dry-run`, depois sem `--dry-run`.
4. Confirme em **Privacidade e Segurança**: 0 ferramentas, memória desligada. O bloqueio `pricing_missing` permanece até haver provedor e preços aprovados.
5. **Inferência real continua não autorizada.** Não configure provedores nem memória externa sem decisão explícita.

## 12. Segredos a configurar manualmente (nunca no Git)

- `gh auth login` e logins de Claude Code, Codex e Antigravity.
- `HORUS_API_TOKEN` gerado a cada sessão do backend (passo 5).
- Chave da API do Hermes e `integrations\hermes.json`: gerados localmente pelo `Prepare-Hermes.ps1`.
- `.env` da fábrica: ElevenLabs, YouTube, Pexels, Pixabay.
- Futuramente: credencial do provedor de IA, dedicada e com teto financeiro no próprio provedor.

## 13. Gestão no GitHub

- Project: <https://github.com/users/yannbastos/projects/1> (privado).
- Issues: <https://github.com/yannbastos/horus-management/issues> (privado).
- Procedimento: [GESTAO-GITHUB.md](GESTAO-GITHUB.md). Consulte prioridade, dependências e trabalho em andamento antes de delegar.

## 14. Limitações conhecidas

- Rede corporativa com inspeção TLS: ferramentas com repositório de certificados próprio (MSYS2, Java, `certifi`, npm/uv em alguns casos) falham até a CA corporativa ser adicionada; downloads de modelos Whisper podem ser bloqueados.
- Sem administrador: Docker, `winget --scope machine` e regras de firewall indisponíveis.
- Hermes sem isolamento no nível do sistema operacional e sem corte financeiro rígido no Hórus (o orçamento é estimado; o corte real precisa existir no provedor).
- Ao iniciar o gateway Hermes, ele pode recriar skills/cron; verifique o status antes de qualquer análise e mantenha o gate fechado se isso ocorrer.
- Instalação NSIS, GPU/CUDA, diálogos nativos e transcrição longa ainda não foram homologados.
- Resíduos antigos em páginas livres do SQLite só saem com `VACUUM` (não executado); use BitLocker no disco do sistema.
- O teste de symlink fica ignorado no Windows sem privilégio de criação de links.

## 15. Retomar a coordenação dos agentes

A orquestração usa Git, Issues e Project. Para preparar somente a coordenação, instale Git, GitHub CLI, Orca e os agentes escolhidos. As dependências de desenvolvimento do produto podem ser instaladas quando necessárias.

1. Clone o Hórus, entre no checkout e escolha o workspace ancestral onde você abre as sessões do Orca. Exemplo para a pasta-mãe imediata:

   ```powershell
   git clone https://github.com/yannbastos/horus.git
   Set-Location horus
   powershell -NoProfile -File ./scripts/bootstrap-horus-orca.ps1 -WorkspacePath '..' -DryRun
   powershell -NoProfile -File ./scripts/bootstrap-horus-orca.ps1 -WorkspacePath '..'
   ```

   Use `-WorkspacePath '.'` para verificar as skills versionadas se abrir na raiz do checkout. Omitir o argumento escolhe a pasta-mãe. Caminhos relativos de `-WorkspacePath` partem da raiz Git detectada. O bootstrap verifica ferramentas, acessos e pontes. Se uma skill manual ocupar o destino, preserve-a e abra na raiz ou escolha outro workspace.

2. Abra o Orca, adicione o checkout e faça os logins locais conforme os passos 1 e 10. Rode o bootstrap com `-DryRun` novamente para conferir acessos. Confirme o login do Antigravity na própria CLI.
3. Abra uma sessão Claude Code dedicada em um terminal do checkout no Orca e invoque `/supervisar`, quando quiser usar o Supervisor. Ele recupera Issues, Project, checkpoints e eventos de coordenação e usa o Run de supervisão desta instalação.
4. Abra outra sessão e invoque `/horus-maestro` (ou siga o [procedimento manual](RETOMADA-MAESTRO.md)). O Maestro reconcilia as referências publicadas, decisões, pendências e atividade [antes de recriar worktrees e Runs](RETOMADA-MAESTRO.md#e-resumo-e-retomada). Essa etapa também funciona sem Supervisor.
5. Antes de sair da máquina antiga, publique a branch quando autorizado e registre o que ficou exclusivamente local. Na nova máquina, recrie sessões, terminais e Runs sob demanda. O [handoff de um Run vivo](RETOMADA-MAESTRO.md#supervisor-e-handoff-no-orca) funciona entre sessões da mesma instalação. Modelos e ferramentas locais necessários devem ser obtidos novamente, conferindo os hashes documentados.

## Verificar e atualizar CLIs de desenvolvimento

Opcionalmente, registre o comando global de usuário `atualizar-ias` nos perfis do PowerShell 5.1 e 7 (sem admin, sem alterar `PATH` nem `ExecutionPolicy`):

```powershell
powershell -NoProfile -File scripts/install-atualizar-ias.ps1
```

Depois disso, em qualquer pasta, `atualizar-ias` executa a verificação (`-Check`) e `atualizar-ias -All` executa as atualizações elegíveis (após autorização e sem sessões ativas). Na raiz do checkout, também é possível executar diretamente:

```powershell
powershell -NoProfile -File scripts/update-dev-tools.ps1 -Check
```

Com autorização de atualização e depois de encerrar as sessões, tente todas as ferramentas elegíveis:

```powershell
powershell -NoProfile -File scripts/update-dev-tools.ps1 -All -NonInteractive
```

O script preserva configurações, credenciais, modelos, memória e PATH; pula sessões ativas, conflitos e instalações de TI. Atualizações nativas cujo canal ou efeitos não possam ser garantidos ficam manuais. Métodos, fontes oficiais, simulação, testes e agendador opcional estão em [dev-tools-update.md](dev-tools-update.md). Registrar tarefa de logon exige autorização específica; o plano padrão apenas verifica e não foi registrado automaticamente.
