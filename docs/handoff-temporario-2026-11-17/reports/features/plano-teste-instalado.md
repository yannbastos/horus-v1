# Plano de Testes — Hórus Instalado em Ambiente Isolado

**Data:** 2026-11-17  
**Base de código analisada:** `C:\Users\yannn\01. Projetos\HORUS\horus` (`main`, commit `60e1426`)  
**Natureza deste documento:** **somente planejamento**. Nenhuma instalação, build, execução de aplicativo, ativação de recurso do Windows ou leitura de dados operacionais foi realizada.

> **Separação entre fato verificado e estimativa/hipótese:**  
> - **[Fato]** indica informação verificada diretamente nos documentos, scripts ou issues do repositório citados com `arquivo:linha`.  
> - **[Estimativa]** ou **[Hipótese]** indica projeção de tempo, custo ou comportamento ainda não medido em máquina limpa.

---

## 1. Objetivo e escopo

### Objetivo
Validar o comportamento ponta a ponta do **Hórus instalado (Tauri 2 + NSIS + backend PyInstaller `onedir`)** em ambiente descartável e isolado, utilizando exclusivamente **dados sintéticos**, **sem rede externa**, **sem credenciais reais** e **sem consumo de APIs pagas**, antes de qualquer execução na máquina operacional ou liberação de uso cotidiano.

### O que SERÁ testado (em ambiente isolado)
- Instalação por usuário (`currentUser`) sem elevação (UAC) e destino padrão `%LOCALAPPDATA%\Programs\Horus` ([Fato] `docs/empacotamento-windows.md:318`, `#134`).
- Inicialização do desktop instalado, saúde do backend filho autenticado e encerramento limpo sem processos órfãos ([Fato] `docs/empacotamento-windows.md:204-229`).
- Interface no WebView2 instalado (incluindo tela **Ajustes** e regressão de rolagem/área branca da `HOR-148`, `#135`).
- Fluxo sintético de produção local até MP4/QC, transcrição offline (modelo ausente ou local fictício), checkpoints humanos, auditoria de transmissões, cofre/privacidade, exclusão segura com quarentena, backup/restauração e migração sintética `v9 → v12`.
- Ciclo de atualização `N → N+1`, rollback manual e desinstalação preservando dados (`HOR-147`, `HOR-100`).
- Prova de isolamento de rede (ausência de tráfego fora da allowlist/loopback).

### O que NÃO será testado neste plano
| Item fora de escopo | Motivo / Referência |
|---|---|
| **Inferência real do Hermes** | Exige autorização explícita, provedor e definição de preços; bloqueado por padrão (`pricing_missing` / gate de segurança) ([Fato] `docs/AGENT-GOVERNANCE.md:36`, `docs/CONTINUAR-EM-OUTRO-PC.md:140-141`). |
| **Serviços pagos** (ElevenLabs, provedores de IA) | Exigem credencial real no cofre, `HORUS_NARRATION_PAID_ENABLED=1` e autorização financeira do usuário ([Fato] `docs/seguranca-dados.md:191`). |
| **Publicação externa** (YouTube/uploads) | Publicação automática não existe por desenho (`publishable=false`); qualquer publicação exige decisão humana ([Fato] `docs/politica-destinos-e-ameacas.md:10,77`). |
| **Dados operacionais reais** (`com.horus.audiovisual`, `Horus\workspace`, mídias do operador) | Proibido usar dados reais em ensaios de instalador ([Fato] `docs/empacotamento-windows.md:206,389-391`). |
| **Canal real do updater (HTTPS/proxy privado)** | Canal remoto e custódia definitiva da chave minisign continuam pendentes de decisão ([Fato] `docs/adr-atualizador.md:43-46,193-197`). |
| **Assinatura Authenticode** | Adiada para distribuição pública; alertas de SmartScreen no Windows real não são resolvidos por minisign ([Fato] `docs/adr-atualizador.md:23,50`). |

---

## 2. Opções de ambiente isolado comparadas

| Critério | Opção A: VM Hyper-V com checkpoint | Opção B: Windows Sandbox (`.wsb`) | Opção C: Host local com identidade isolada (`HORUS Test`) |
|---|---|---|---|
| **Identidade testada** | Produção real (`HORUS` / `com.horus.audiovisual`) dentro da VM ([Fato] `docs/empacotamento-windows.md:396-399`). | Produção real (`HORUS` / `com.horus.audiovisual`) dentro do Sandbox ([Fato] `docs/empacotamento-windows.md:486-487`). | Somente teste (`HORUS Test` / `com.horus.installer-test`) ([Fato] `docs/empacotamento-windows.md:342-344`). |
| **Pré-requisitos reais** | 1. Windows Pro/Enterprise/Education com Hyper-V habilitado pelo usuário (exige admin + reinício).<br>2. ISO/imagem Windows x64 limpa.<br>3. **WebView2 Evergreen Standalone x64** instalado offline na VM.<br>4. Usuário padrão (não-admin) criado na VM, resolução ≥ 1920×1080, rede desconectada.<br>5. ISO somente leitura com `input/` ([Fato] `docs/empacotamento-windows.md:495-513,535-545`). | 1. Windows Pro/Enterprise com recurso **Windows Sandbox** habilitado pelo usuário (exige admin + reinício).<br>2. Imagem padrão **pode não ter** WebView2 Evergreen nem sessão não-elevada: se a sessão padrão for admin, `guest.ps1` aborta e exige criar/executar como usuário padrão (`runas /profile`) ([Fato] `docs/empacotamento-windows.md:503-534`). | 1. Sem VM/Sandbox.<br>2. Requer `HORUS Test` compilado com `scripts/tauri-installer-test.json` + `-E2eRoot` sob `%TEMP%` para isolar WebView/dados.<br>3. Nenhum produto `HORUS Test` prévio no registro HKCU ([Fato] `docs/empacotamento-windows.md:342-358`). |
| **Custo / tempo estimado** | **[Estimativa]** 15–60 min para build N/N+1 no host (`prepare-host.ps1 -Build`, vários GiB em `%TEMP%`) ([Fato] `docs/empacotamento-windows.md:490-493`) + **[Estimativa]** 40–90 min na 1ª vez para criar VM, instalar Windows + WebView2 offline e tirar checkpoint + 10–20 min por bateria. | **[Estimativa]** 15–60 min para build N/N+1 + **[Estimativa]** 5–15 min por execução se a imagem já atender aos pré-requisitos; porém alto risco de retrabalho se faltar WebView2 ou usuário padrão a cada boot. | **[Estimativa]** 15–30 min para build de teste + 5–10 min de execução automatizada (`test-installer-data-isolation.ps1` / `test-updater-e2e.ps1`). |
| **Garantia sobre perfil real (`com.horus.audiovisual`)** | **Garantia total por isolamento de SO/disco:** a VM não tem pastas compartilhadas com o perfil real; apenas ISO `input/` (somente leitura) e disco/pasta exclusiva de evidências ([Fato] `docs/empacotamento-windows.md:389-391,535-545`). | **Garantia alta se configurado corretamente:** `<Networking>Disable</Networking>` e `<MappedFolders>` mapeando apenas `input/` (ReadOnly) e pasta vazia de evidências ([Fato] `scripts/vm/rehearsal.example.wsb:3-8`). | **Sem isolamento de SO:** roda no mesmo usuário Windows do operador. Depende de disciplina dos scripts (`HORUS Test`, `HORUS_DATA_DIR`, `WEBVIEW2_USER_DATA_FOLDER`). |
| **Riscos residuais** | Nenhum ao perfil real do host, desde que o usuário nunca mapeie pastas pessoais nem restaure dados da VM para o host ([Fato] `docs/empacotamento-windows.md:456,546`). | 1. Se o operador alterar o `.wsb` e mapear pasta pessoal, perde o isolamento.<br>2. Tudo dentro do Sandbox se perde ao fechar (não tem checkpoint intermediário entre fases). | 1. **Risco de colisão dev/instalado:** no host, `desktop:dev` (`tauri.dev.conf.json`) e o app instalado (`tauri.conf.json`) compartilham o mesmo `identifier` (`com.horus.audiovisual`) e a mesma pasta `%LOCALAPPDATA%\com.horus.audiovisual` (`workspace/` e `EBWebView/`) se executados sem override ([Fato] `frontend/src-tauri/tauri.dev.conf.json:1-4`, `docs/adr-atualizador.md:63-74`).<br>2. `test-updater-e2e.ps1` grava por padrão em `%LOCALAPPDATA%\Horus\dev-updater-test` e `dev-reports` ([Fato] `scripts/test-updater-e2e.ps1:9-10`).<br>3. Testes locais sem mock de keyring gravam no Windows Credential Manager do mesmo usuário ([Fato] `docs/seguranca-dados.md:175`). |

### Recomendação
1. **Ambiente principal recomendado: Opção A (VM Hyper-V com checkpoint)** para toda a validação do pacote de produção (`HORUS` / `com.horus.audiovisual`), interface no WebView2, fluxos sintéticos, atualização, rollback e desinstalação. É a única opção que permite **checkpoints entre fases** (ex.: restaurar estado pré-desinstalação ou pré-migração), **usuário padrão persistente**, **WebView2 offline pré-instalado no snapshot base** e **rede desconectada no adaptador virtual** ([Fato] `docs/empacotamento-windows.md:530-550`).
2. **Alternativa rápida (somente se Hyper-V não for viável): Opção B (Windows Sandbox)** apenas para o roteiro `scripts/vm/guest.ps1` se a imagem local já permitir sessão padrão não elevada e WebView2 offline.
3. **Opção C (`HORUS Test` no host):** manter restrita a **regressões automatizadas rápidas** no ciclo de desenvolvimento, nunca como substituto da validação em máquina limpa ([Fato] `docs/empacotamento-windows.md:230-231,398-399`).

---

## 3. Matriz de testes

| ID | O que valida | Passos resumidos | Dado sintético usado | Critério de aceite | Risco | Requer autorização? |
|---|---|---|---|---|---|---|
| **T01** | Instalação padrão e destino (`HOR-147`) | Na VM limpa (usuário padrão), executar `HORUS-N.exe` sem `/D` (interativo) e verificar caminho proposto; concluir sem UAC. Validar também ramo `/S /NS` em snapshot limpo. | Nenhum (VM limpa sem `HORUS` nem `com.horus.audiovisual`). | Propõe `%LOCALAPPDATA%\Programs\Horus`; instala sem UAC; `HKCU:\Software\horus\HORUS` e `Uninstall\HORUS` apontam para esse destino com versão `N` ([Fato] `docs/empacotamento-windows.md:417-421`). | Baixo na VM; alto se rodado no host. | **Sim** (Fase 2) |
| **T02** | Identidade de ensaio isolada (`HOR-147`) | Verificar `ProductName` dos artefatos de VM (`HORUS`) vs regressão estática `test-installer-location.py StaticContract` no host. | Chaves aleatórias de fixture no teste estático ([Fato] `docs/empacotamento-windows.md:367`). | `StaticContract` passa (3 casos); na VM, apenas a identidade `HORUS` é instalada; no host, nenhuma chave `HORUS Test` é criada sem autorização. | Baixo. | **Sim** (para execução na VM) |
| **T03** | Primeira abertura e saúde do backend | Abrir `horus-desktop.exe` instalado na VM; consultar `/health` e `/v1/diagnostics` autenticados; fechar normalmente e em queda simulada. | Workspace vazio inicial criado pelo app na VM. | App abre em ≤ 45 s; `/health` `ok=true`; `/v1/diagnostics` coerente com perfil; zero processos `horus-backend.exe` órfãos após fechar ([Fato] `docs/empacotamento-windows.md:8,221-222`). | Médio (antivírus/cache frio na VM). | **Sim** (Fase 2) |
| **T04** | Tela Ajustes — rolagem única e sem área branca (`HOR-148`) | Abrir app instalado na VM, maximizar janela, navegar para **Ajustes** (via UI ou CDP `127.0.0.1:19447`), testar também largura mínima (`1040×680`), capturar PNG e medir DOM. | Nenhum conteúdo real; apenas UI do app. | `scrollHeight <= innerHeight + 1`, `scrollWidth <= innerWidth + 1`, `whiteSamples == 0`, rolagem interna funciona em seções longas; aprovado em revisão visual humana das capturas ([Fato] `scripts/vm/guest.ps1:95-103`, `#135`). | Baixo. | **Sim** (Fase 2) |
| **T05** | Primeiro vídeo SINTÉTICO até MP4/QC | Com fábrica local preparada, FFmpeg local e narração local (Kokoro ou SAPI sintético; sem Hermes real nem ElevenLabs), criar proposta/roteiro fictícios, gerar `local_pilot`, rodar QC, revisão por hash e exportar ZIP. | Roteiro fictício, fixtures de licença/aprovação, áudio sintético ([Fato] `docs/empacotamento-windows.md:84-92,124-133`). | `production.ready=true`; gera MP4 e `quality-report.json` sem erros bloqueantes; `publishable=false` mantido no export ZIP. | Médio (exige pacote com transcrição + recursos externos copiados para a VM). | **Sim** (Fase 3) |
| **T06** | Transcrição com modelo Whisper ausente ou local fictício | 1. Sem modelo importado: verificar diagnóstico e recusa limpa sem tentar download.<br>2. Com pasta `small` local pré-copiada na VM: importar via `/v1/transcription/models/import` e transcrever WAV sintético em CPU `int8`. | WAV sintético (ex.: fala SAPI/silêncio) e pesos locais offline (sem rede). | Nenhuma conexão ao Hugging Face; diagnóstico indica modelo ausente quando não importado; transcrição conclui em CPU `int8` quando modelo local está presente ([Fato] `docs/empacotamento-windows.md:166-170`). | Médio (depende do build com perfil `--extra transcription`). | **Sim** (Fase 3) |
| **T07** | Checkpoints humanos e limites (`HOR-158` / `HOR-107`) | Tentar acionar ação protegida (`paid_narration` ou `publish`) sem checkpoint, com checkpoint expirado ou já consumido; depois criar checkpoint válido `POST /v1/checkpoints` e verificar consumo único. | Payload sintético com hash fictício e limite em USD simulado. | Sem checkpoint válido retorna HTTP 409 `security_gate` e registra `gate_refusal`; checkpoint válido é consumido uma única vez ([Fato] `docs/politica-destinos-e-ameacas.md:28`). | Baixo. | **Sim** (Fase 3) |
| **T08** | Auditoria de transmissões (`HOR-115`) | Consultar `GET /v1/security/transmissions` após ações simuladas e recusas de gate (T07). | Eventos sintéticos de gate/egress na VM. | Tabela `transmissions` (schema ≥ 11) registra estágio, destino loopback, tamanho e SHA-256 sem expor conteúdo de roteiro/segredo ([Fato] `docs/checklist-pre-inferencia.md:72`). **Ver lacuna §7 sobre presença na `main`.** | Médio (verificar se está integrado no build atual). | **Sim** (Fase 3) |
| **T09** | Cofre, privacidade e status de segurança | Abrir **Ajustes → Privacidade e Segurança** (`GET /v1/security/status`); testar arquivo `integrations/hermes.json` fictício apontando para `127.0.0.1`; verificar `disk_encryption` no diagnóstico. | Token sintético descartável e URL `http://127.0.0.1:8642` inexistente. | UI exibe 0 ferramentas, memória desligada, execução real bloqueada (`pricing_missing`); nenhum segredo aparece em logs, respostas ou exports ([Fato] `docs/CONTINUAR-EM-OUTRO-PC.md:140`, `docs/seguranca-dados.md:168-192`). | Baixo. | **Sim** (Fase 2) |
| **T10** | Exclusão segura de dados (`HOR-117`) | Criar produção/importação sintética; chamar `POST /v1/data-deletion/preview`, executar `execute` com `intent_token` + frase exata; testar bloqueio (409) com job ativo; restaurar da quarentena e checar `deletion-audit.json`. | Produção e importação sintéticas criadas em T05/T06. | Gera backup pré-exclusão verificado fora da pasta de dados; move para `.quarentena`; cadeia SHA-256 em `deletion-audit.json` íntegra; `restore` devolve arquivos íntegros ([Fato] `docs/seguranca-dados.md:143-152`). | Baixo. | **Sim** (Fase 3) |
| **T11** | Backup/restauração e migração `v9 → v12` (`HOR-157`) | Dentro da VM, executar `scripts/ensaio_atualizacao_banco.py` em pasta temporária; testar `local_data_export export/verify/restore`, `backups verify/restore`, migração `v9→v12`, idempotência e rollback para cópia `v9`. | Banco `v9` sintético gerado pelo próprio script (mídia, workflow, job, roteiro, Radar fictícios). | Todos os passos imprimem `PASSOU`; `PRAGMA integrity_check` = `ok`; `user_version` = `12`; hashes lógicos das tabelas `v9` preservados; backup `horus.backup-v9-*` íntegro ([Fato] `docs/procedimento-atualizacao-banco-v9-v12.md:25-78`). | Médio (requer Python/venv na VM ou validação equivalente). | **Sim** (Fase 3) |
| **T12** | Atualização `N → N+1` e rollback | **Ramo A (NSIS direto):** na VM com dados semeados e fechada, rodar `HORUS-N1.exe /S /NS /UPDATE`; validar versão `N+1` e dados; restaurar snapshot ou reinstalar `N` (schema compatível) para rollback.<br>**Ramo B (Plugin updater, opcional):** servir canal local em `127.0.0.1` com chave de teste dentro da VM e confirmar via UI. | SQLite, projeto JSON, mídia binária e preferências sintéticas (`guest.ps1:73-79`) + hashes antes/depois. | Versão `N+1` ativa, `integrity_check=ok`, 100% dos hashes preservados; no rollback manual, `N` reabre sem corrupção; downgrade incompatível de schema é recusado ([Fato] `docs/empacotamento-windows.md:261-269,445-448`, `docs/adr-atualizador.md:337-346`). | Médio/Alto (Ramo B exige build com overlay de teste). | **Sim** (Fase 4) |
| **T13** | Desinstalação preservando dados (`HOR-147`) | 1. Executar `uninstall.exe` interativo, confirmar ausência de checkbox de exclusão e concluir.<br>2. Restaurar snapshot da VM e repetir com `uninstall.exe /S`. | Mesmos dados sintéticos de T12 em `%LOCALAPPDATA%\com.horus.audiovisual` e `%APPDATA%\com.horus.audiovisual`. | Executáveis e chave `Uninstall\HORUS` removidos; **nenhum** arquivo/hash das pastas de dados alterado ou apagado ([Fato] `docs/empacotamento-windows.md:449-452,559-563`). | Baixo na VM. | **Sim** (Fase 4) |
| **T14** | Prova de rede (allowlist / zero tráfego externo) | Manter adaptador de rede da VM desconectado (ou regras de firewall bloqueando tudo exceto `127.0.0.1`) durante todas as fases; registrar tentativas de conexão/DNS e validar `test_network_inventory.py` no host. | Tráfego gerado durante T03–T13. | Zero falhas funcionais por bloqueio de rede nos fluxos locais; zero tentativas de conexão externa registradas; `test_network_inventory.py` aprovado ([Fato] `docs/politica-destinos-e-ameacas.md:9,18-20`). | Baixo. | **Sim** (contínuo Fases 2–4) |

---

## 4. Dados sintéticos

### Como gerar (ferramentas existentes vs. ausentes)
- **Já existentes no repositório:**
  - `scripts/vm/guest.ps1` + `guest-support.ps1`: cria automaticamente banco SQLite sintético via `winsqlite3.dll` (`[VmNative]::Database`), `vm-fixture-project.json`, `vm-fixture-preferences.json` e `vm-fixture-media.bin` ([Fato] `scripts/vm/guest.ps1:73-79`).
  - `scripts/ensaio_atualizacao_banco.py`: constrói banco `v9` sintético completo (com `media`, `workflows`, `jobs`, `job_attempts`, `artifacts`, `scripts`, `script_receipts` e Radar sintético) exclusivamente sob `%TEMP%` ([Fato] `docs/procedimento-atualizacao-banco-v9-v12.md:25,132`).
  - `scripts/test-installed-first-video.py`: contém fixtures de proposta, roteiro fictício e revisão por hash para o fluxo de vídeo ([Fato] `docs/empacotamento-windows.md:84-92`).
- **Ausentes / precisam ser preparados para a VM:**
  - **WAV sintético curto** (ex.: 5–20 s gerado localmente por FFmpeg/SAPI em pasta temporária) para T06, caso se teste transcrição com modelo local ([Fato] `docs/empacotamento-windows.md:178`).
  - **Pacote de entrada de produção (T05/T06):** se T05 e T06 forem executados na VM, é necessário copiar para a ISO somente leitura os recursos externos já preparados localmente (fábrica `fabrica-canais`, `ffmpeg.exe`, Kokoro e modelo Whisper `small`), além de um build com `--config ../scripts/tauri-first-video.json` ou `--config ../scripts/tauri-transcription.json` ([Fato] `docs/empacotamento-windows.md:49,77-81,347`).

### O que conter e onde ficam
- **Conteúdo:** exclusivamente textos fictícios ("Projeto fictício"), bytes binários curtos (`0x01..0x04`), tokens aleatórios descartáveis e bancos gerados do zero no ensaio. **Proibido** copiar qualquer arquivo de `%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus` ou `.env` reais.
- **Localização:** exclusivamente dentro da VM (em `%LOCALAPPDATA%\com.horus.audiovisual\workspace` do usuário padrão da VM e em `C:\Ensaio\` / `%TEMP%` da VM). Tudo é destruído com a VM ao final.

---

## 5. Controles de segurança do ensaio

1. **Rede:**
   - **VM Hyper-V:** desconectar o adaptador de rede virtual antes de criar o checkpoint base ([Fato] `docs/empacotamento-windows.md:536`). No Ramo B de T12 (se testado), usar apenas loopback `127.0.0.1` interno à VM.
   - **Windows Sandbox:** `<Networking>Disable</Networking>` e `<ClipboardRedirection>Disable</ClipboardRedirection>` ([Fato] `scripts/vm/rehearsal.example.wsb:3-4`).
2. **Sem credenciais nem chaves reais:** nenhuma chave de ElevenLabs, YouTube, Pexels, Pixabay ou provedor de IA entra na VM ou nos scripts. Token do Hermes, se testado em T09, é string sintética descartável apontando para `127.0.0.1`.
3. **Padrões seguros verificados:** confirmar em `/v1/security/status` e `/v1/diagnostics` que:
   - `HORUS_WORKFLOW_EXECUTOR=simulated` e `HORUS_RADAR_EXECUTOR=hermes_simulated` ([Fato] `docs/CONTINUAR-EM-OUTRO-PC.md:71-72`);
   - `HORUS_NARRATION_PAID_ENABLED` ausente/desligada e `HORUS_PRICE_*` ausentes (`pricing_missing`) ([Fato] `docs/CONTINUAR-EM-OUTRO-PC.md:75`, `docs/seguranca-dados.md:191`);
   - `publishable=false` em toda exportação ([Fato] `docs/empacotamento-windows.md:130`).
4. **Checkpoints obrigatórios:** criar checkpoint na VM **antes** de cada fase (Fase 2, Fase 3, Fase 4 e antes de cada ramo de desinstalação interativa vs `/S`).
5. **Critérios de aborto (NO-GO imediato):**
   - Qualquer execução detectada fora da VM/Sandbox quando o alvo for identidade de produção (`guest.ps1` já aborta se `hostComputer == COMPUTERNAME` ou se houver token elevado) ([Fato] `scripts/vm/guest.ps1:11,18`).
   - Solicitação inesperada de UAC durante instalação, atualização ou desinstalação.
   - Qualquer tentativa de conexão de rede externa observada nos logs/captura da VM.
   - Divergência de SHA-256 em fixtures após atualização/desinstalação ou falha em `PRAGMA integrity_check`.
6. **Evidências a registrar e onde guardar:**
   - **Registrar:** SHA-256 dos instaladores `N` e `N+1`, `results.json`, `transcript.log`, `settings-layout.json`, capturas PNG (`settings-webview.png`, `settings-maximized-desktop.png`, `uninstall-interactive-no-checkbox.png`), `fixture-hashes-before.json`, `fixture-hashes-after.json`, relatórios de `PRAGMA integrity_check` e saída de T05–T14.
   - **Onde guardar:** copiar da pasta/disco de evidências da VM para pasta dedicada fora do repositório no host (ex.: `C:\Users\yannn\AppData\Local\Horus\dev-reports\auditoria-contexto-2026-11-17\evidencias-instalado\`), **nunca** versionar no Git.

---

## 6. Sequência proposta em fases e portões de autorização

| Fase | Escopo e testes | Ações / comandos concretos | Quem executa | Impacto na máquina host | Autorização necessária do usuário |
|---|---|---|---|---|---|
| **Fase 0 — Preparação e preflight (somente leitura)** | Conferir ferramentas no host e validar scripts estáticos sem build nem instalação. | 1. `powershell -NoProfile -File scripts/vm/prepare-host.ps1` (somente consulta).<br>2. `python scripts/vm/test_vm_rehearsal.py`.<br>3. `python scripts/test-installer-location.py StaticContract`. | Codex / Antigravity | **Zero:** não compila, não instala, não altera registro nem dados. | **Não** (somente leitura/testes unitários isolados; já dentro da governança). |
| **Fase 1 — Criação do ambiente isolado e pacotes** | Habilitar VM Hyper-V, instalar WebView2 offline, criar usuário padrão e checkpoint; compilar pacotes `N` e `N+1` em `%TEMP%`. | **Usuário:** habilitar Hyper-V (se inativo), criar VM Windows x64, instalar WebView2 Evergreen Standalone x64, criar usuário padrão, desconectar rede, gerar checkpoint `Base-Limpa`.<br>**Agente (após OK):** `powershell -NoProfile -File scripts/vm/prepare-host.ps1 -Build -VersionN 0.1.0 -VersionNext 0.1.1` (gera `input/` em `%TEMP%\horus-vm-input-<uuid>\`). Gerar ISO somente leitura de `input/`. | **Usuário** (VM/Hyper-V/WebView2) + **Codex** (build em `%TEMP%` se autorizado). | **Médio temporário:** build consome CPU/RAM (~15–60 min) e alguns GiB em `%TEMP%` e `backend/.venv-bundle*`; **zero impacto** no perfil real. | **Sim — Portão 1:** autorizar habilitação de Hyper-V/criação da VM, download/instalação do WebView2 Standalone e execução do build `-Build` no host. |
| **Fase 2 — Instalação e testes básicos (VM)** | Executar **T01, T02, T03, T04, T09, T14** na VM a partir do checkpoint `Base-Limpa`. | Na VM (sessão padrão não elevada): rodar parte inicial de `guest.ps1` (instalação `N`, checagem de destino/chaves HKCU, abertura maximizada, captura/DOM de Ajustes `HOR-148`, verificação de privacidade/segurança e rede). Criar checkpoint `Pos-Basico`. | **Usuário** (inicia na VM ou acompanha) / **Antigravity** (revisão visual das capturas PNG). | **Zero** no host (tudo ocorre dentro da VM). | **Sim — Portão 2:** autorizar início do ensaio na VM com os pacotes `N`/`N+1`. |
| **Fase 3 — Fluxos sintéticos, segurança e banco (VM)** | Executar **T05, T06, T07, T08, T10, T11, T14** na VM (a partir de `Pos-Basico` ou checkpoint dedicado). | Montar recursos sintéticos (fábrica/FFmpeg/WAV/modelo local se incluídos) em ISO somente leitura; validar produção sintética, transcrição offline, checkpoints humanos, auditoria de transmissões, exclusão segura/quarentena e `ensaio_atualizacao_banco.py` (`v9 → v12`). | **Codex** (roteiro técnico na VM / análise de logs) + **Antigravity** (validação UI). | **Zero** no host (exceto espaço em disco temporário da ISO de recursos). | **Sim — Portão 3:** autorizar cópia dos recursos externos preparados (fábrica/modelos) para a ISO da VM e execução da bateria sintética completa. |
| **Fase 4 — Atualização, rollback, desinstalação e descarte** | Executar **T12, T13, T14**, coletar evidências e destruir/restaurar VM. | Executar ciclo `N → N+1` (`/S /NS /UPDATE`), rollback manual (via snapshot ou `N`), desinstalação interativa (sem checkbox) e silenciosa `/S` (restaurando snapshot intermediário); copiar evidências para fora da VM; destruir VM/limpar `%TEMP%\horus-vm-input-*`. | **Usuário** (descarte final da VM) + **Codex/Antigravity** (consolidação do relatório de evidências). | **Zero** no perfil real; libera espaço em `%TEMP%` após coleta. | **Sim — Portão 4:** autorizar encerramento, coleta de evidências e descarte da VM. |

---

## 7. Lacunas, riscos e perguntas para o usuário

### Lacunas e pontos desatualizados identificados nos documentos
1. **Desinstalador interativo (`empacotamento-windows.md`):** a linha `docs/empacotamento-windows.md:256-259` ainda afirma que *"O template NSIS atual pode apagar dados quando o usuário marca a caixa de exclusão na desinstalação interativa"*, o que foi superado pela `HOR-147` documentada mais abaixo no mesmo arquivo (`docs/empacotamento-windows.md:381-387`) e em `docs/adr-atualizador.md:87-91` ([Fato]).
2. **Pasta legada vs. nova (`migracao-local.md`):** `docs/migracao-local.md:286` ainda menciona `%LOCALAPPDATA%\HORUS` e a caixa "excluir dados", anterior à mudança para `%LOCALAPPDATA%\Programs\Horus` na `HOR-147` ([Fato]).
3. **Auditoria de transmissões (`HOR-115`):** `docs/politica-destinos-e-ameacas.md:68` cita `feat-hor-115` como branch retida ainda não na `main`, enquanto `docs/checklist-pre-inferencia.md:72` a marca como `IMPL.` (migração `v11`) e `docs/procedimento-atualizacao-banco-v9-v12.md:72` confirma a tabela `transmissions` na `v11`. É necessário confirmar se todos os pontos de T08 estão ativos no build `main` `60e1426` antes da Fase 3 ([Fato]).
4. **Cobertura atual do `scripts/vm/guest.ps1`:** o script automatiza **T01, T03 (parcial), T04, T12 (ramo NSIS direto) e T13**, mas **não** executa primeiro vídeo (T05), transcrição (T06), checkpoints/transmissões (T07–T08), exclusão segura (T10), migração `v9 → v12` (T11) nem o plugin updater (T12 Ramo B) ([Fato] `docs/empacotamento-windows.md:571-573`).
5. **Padrões de `scripts/test-updater-e2e.ps1`:** sem parâmetros explícitos, grava canal e artefatos em `%LOCALAPPDATA%\Horus\dev-updater-test` e `dev-reports` no host ([Fato] `scripts/test-updater-e2e.ps1:9-10`). Não deve ser usado no host sem parametrização explícita.
6. **Pré-requisitos ausentes hoje:** não há instaladores `0.1.0`/`0.1.1` compilados da `main` (`60e1426`) prontos para VM, nem confirmação de Hyper-V/Sandbox habilitados ou de posse do **WebView2 Evergreen Standalone Installer x64** offline ([Fato] `docs/empacotamento-windows.md:503-513`).

### Perguntas para o usuário antes de iniciar a Fase 1
1. **Ambiente escolhido:** você prefere preparar uma **VM Hyper-V** (recomendado, com checkpoint e usuário padrão) ou tentar primeiro o **Windows Sandbox**? Hyper-V já está habilitado nesta máquina?
2. **Escopo do build de ensaio:** na Fase 1, deseja testar apenas o **pacote mínimo** (`guest.ps1`: T01–T04, T09, T12 NSIS, T13, T14) ou também gerar o **pacote completo com transcrição/primeiro vídeo** (T05, T06) levando fábrica/FFmpeg/Kokoro/modelo `small` local para a ISO da VM?
3. **Plugin updater (T12 Ramo B):** deseja incluir um ciclo extra com **canal loopback + chave minisign de teste** dentro da VM para validar a UI de **Ajustes → Atualizações**, ou o ciclo NSIS `/UPDATE` (Ramo A) é suficiente nesta rodada?
4. **Versão base `N`:** podemos usar `0.1.0` → `0.1.1` (alinhado ao `frontend/package.json` atual em `main`) para os artefatos descartáveis da VM?
