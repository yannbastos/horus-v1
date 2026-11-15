# Política de destinos e modelo de ameaças

Issue de gestão: [HOR-109](https://github.com/yannbastos/horus-management/issues/97) (fecha o item **C10** do [checklist pré-inferência](checklist-pre-inferencia.md)). Baseia-se no [inventário de fluxos](inventario-fluxos-dados.md) (F-01..F-19, levantamento de 14/11/2026). **É política e análise, não autorização:** não libera Hermes real, ElevenLabs, coleta nem download externo. Controles técnicos detalhados ficam em [segurança dos dados](seguranca-dados.md) e [segurança do Hermes](seguranca-hermes.md); aqui só se aplicam.

## 1. Princípios aplicados ao Hórus

- **Privacy First / Local First:** roteiro, áudio, vídeo, perfis e licenças nascem e ficam na máquina. Toda função essencial (escrever, narrar com Kokoro/Maria, renderizar, revisar, exportar) funciona sem nuvem.
- **Least Privilege:** processos de produção recebem ambiente mínimo, caminhos confinados e prazo; o Hermes roda sem ferramentas nem memória externa; o token local nunca vai ao frontend nem a terceiros.
- **Deny by Default:** o que não está na `ALLOWLIST` de [`test_network_inventory.py`](../backend/tests/test_network_inventory.py) reprova o teste; o que sai da máquina exige ação humana específica e, para o Hermes, o gate de segurança.
- **Humano decide:** nada é publicado, aprovado, licenciado ou pago por agente ou automação.

## 2. Política de destinos

Todo fluxo se enquadra em exatamente uma classe. Um fluxo novo sem classe **não pode ser integrado** (o teste de inventário o barra).

| Classe | Regra | Fluxos do inventário |
|---|---|---|
| **A. Nenhum** | Sem rede; processo local com ambiente mínimo e prazo. | F-06 alinhamento, F-07 Kokoro, F-08 SAPI/Piper, F-11 processos de produção, F-13 QC, F-16 demonstração do Radar, F-19 thumbnail (FFmpeg) |
| **B. Loopback** | Só `127.0.0.1`/`::1`, com porta explícita, sem proxy e sem redirect; token local obrigatório quando aplicável. Não é fronteira de dados. | F-01 diagnóstico do Hermes, F-10 API do Hórus, F-12 render (rede de negação + NetLog), F-14 frontend→backend, F-17 scripts de preparação |
| **C. Externo por ação humana** | Sai da máquina somente quando uma pessoa aciona aquela ação, com destino conhecido e sem enviar roteiro/áudio, salvo C-Hermes abaixo. | F-03 coleta YouTube (CLI com `--allow-network`), F-04 download do Whisper, F-05 yt-dlp, **F-02 envelope do Hermes** |
| **D. Externo bloqueado** | Existe no código, mas sem transporte ou com chave desligada; abrir exige autorização do idealizador. | F-09 ElevenLabs, F-15 Pexels/Pixabay |
| **N. Não verificado** | Fora do repositório; tratar como externo até prova. | F-18 CLI da fábrica |

**C-Hermes (S-4).** O endereço do gateway é loopback, mas o Hermes encaminha o **envelope** (objetivo, perfil do canal, dataset público do Radar, restrições, proposta aprovada, títulos de fontes) a um provedor de modelo. Portanto o envelope é tratado como **dado enviado a destino externo**: exige autorização humana por execução, gate de segurança aberto, reserva de orçamento, registro em `cognitive_runs` e, quando integrado, o [registro de transmissões](checklist-pre-inferencia.md) (C5, em branch retida). Roteiro final, áudio, caminhos locais e credenciais **nunca** entram no envelope. Retenção/treinamento do provedor seguem abertos em C8.

**Gateway de saída (HOR-111, F-20).** Todo destino HTTP do backend (Hermes, download do yt-dlp, Radar) passa por `egress.gateway()`: negação por padrão; regras explícitas em `DEFAULT_RULES` (hosts do YouTube e `www.googleapis.com`) mais `HORUS_EGRESS_ALLOW` (`finalidade=https://host[:porta][,auth][,loopback][,methods=GET+POST]`); o Hermes só vale como `http` em `127.0.0.1`/`::1` com porta explícita. `request()` resolve o DNS uma vez, recusa IP privado/loopback/link-local/reservado (inclusive IPv4 mapeado em IPv6) e conecta ao IP validado (fecha DNS rebinding); redirects só para destinos também permitidos e nunca levam `Authorization` a outra origem; carga e query com caminho local ou credencial são recusadas; cada decisão vira linha em `transmissions` (stage `egress:<finalidade>`, só destino, tamanho e SHA-256). Limites declarados: o gateway **autoriza e registra** (sem rede própria) os clientes que já têm transporte (Hermes em loopback, Radar) e o destino inicial do yt-dlp; o yt-dlp e o download de modelos Hugging Face (S-1) fazem a própria rede dentro do subprocesso, e a resolução de DNS deles não é fixada pelo gateway. Provedor/modelo para onde o Hermes encaminha continua configuração do perfil Hermes (C1/C6).

**Decisões pendentes do idealizador (não decididas aqui).**

| Achado | Situação atual | Opções a decidir |
|---|---|---|
| **S-1** download do modelo Whisper (F-04) | Ativo no primeiro uso, sem gate; o áudio não sai, mas o app contata o Hugging Face. | (a) aceitar como classe C, só documentando; (b) exigir modelo pré-instalado e `local_files_only`; (c) gate de ação humana antes do download. |
| **S-3** yt-dlp (F-05) | Ação humana com validação de URL, sem gate; coleta externa disponível ao operador. | (a) aceitar como classe C; (b) desligar por padrão; (c) gate explícito. |

Até a decisão, ambos permanecem como estão e **não devem ser ampliados**. S-5 (rede do Kokoro por interpretador; Piper/SAPI sem auditoria própria) continua informativo.

## 3. Modelo de ameaças (STRIDE enxuto)

Legenda: S spoofing, T tampering, R repudiation, I information disclosure, D denial of service, E elevation of privilege. “Prova” é o teste que demonstra o controle. Lacuna = sem controle suficiente; vai para a Issue indicada.

### 3.1 Backend local (API)

| Ameaça | Controle existente | Prova | Lacuna / Issue |
|---|---|---|---|
| S: outro processo ou página usa a API | Token ≥ 24 caracteres, comparação em tempo constante, Host/Origin loopback, CORS fixo, bind só em `127.0.0.1` | [`test_endpoint_auth.py`](../backend/tests/test_endpoint_auth.py) | Qualquer processo do mesmo usuário que conheça o token é confiável (documentado em [segurança dos dados](seguranca-dados.md)) |
| T/D: corpo gigante, ID malicioso | Teto de corpo 2 MiB (rotas específicas maiores), IDs validados, caminhos confinados | [`test_body_limit.py`](../backend/tests/test_body_limit.py), [`test_endpoint_auth.py`](../backend/tests/test_endpoint_auth.py) | O corpo aceito ainda é lido inteiro para validar (baixa) |
| R: falta de trilha | Eventos de workflow/produção persistidos; recibos idempotentes | [`test_workflows.py`](../backend/tests/test_workflows.py) | Trilha de transmissões externas: C5/[HOR-115](https://github.com/yannbastos/horus-management/issues/103) |
| I: dados em disco | SQLite com `secure_delete`, redação e retenção do Radar | [`test_sqlite_hardening.py`](../backend/tests/test_sqlite_hardening.py), [`test_retention.py`](../backend/tests/test_retention.py) | Sem cifragem em repouso: C7/[HOR-114](https://github.com/yannbastos/horus-management/issues/102) |

### 3.2 Worker de produção (render, vozes, QC)

| Ameaça | Controle existente | Prova | Lacuna / Issue |
|---|---|---|---|
| T/E: subprocesso lê segredos ou executa comando arbitrário | Ambiente mínimo por operação, operações enumeradas, sem shell, árvore encerrada ao cancelar | [`test_job_environment.py`](../backend/tests/test_job_environment.py) | Binários externos (Piper/SAPI/fábrica) sem auditoria de rede: S-5, F-18 |
| I: render ou voz acessa a rede | Proxy de negação + NetLog do Chromium; Kokoro com bloqueio de sockets e modo offline | [`test_production_network.py`](../backend/tests/test_production_network.py), [`test_narration_kokoro.py`](../backend/tests/test_narration_kokoro.py) | Isolamento de SO/rede: C1/[HOR-124](https://github.com/yannbastos/horus-management/issues/111) |
| D: renders simultâneos ou travados | Fila, timeout, cancelamento e reconciliação após reinício | [`test_production.py`](../backend/tests/test_production.py) | — |
| I/new: novo código que abre rede | Análise estática com allowlist ligada ao inventário | [`test_network_inventory.py`](../backend/tests/test_network_inventory.py) | Não cobre binários nem a fábrica (limite declarado) |

### 3.3 Hermes e provedor externo

| Ameaça | Controle existente | Prova | Lacuna / Issue |
|---|---|---|---|
| E/T: injeção faz o agente usar ferramentas ou obedecer texto de fonte | Perfil de zero ferramentas auditado; fontes entram como dado; saída validada por esquema, fontes permitidas e task_id | [`test_security_profile.py`](../backend/tests/test_security_profile.py), [`test_workflow_hermes.py`](../backend/tests/test_workflow_hermes.py) | Regressões com modelo real só após autorização: C9/[HOR-118](https://github.com/yannbastos/horus-management/issues/106) |
| I: dados além do envelope mínimo | Envelope sem campos extras e sem roteiro/áudio/caminhos | [`contracts/hermes/tests`](../contracts/hermes/tests), [`test_workflow_hermes.py`](../backend/tests/test_workflow_hermes.py) | Retenção/treinamento do provedor: C8/[HOR-116](https://github.com/yannbastos/horus-management/issues/104) |
| D/gasto: execução paga repetida ou sem limite | Autorização por execução, reserva de orçamento, tetos por execução/dia, sem retentativa automática, gate repetido antes do envio | [`test_security_budget.py`](../backend/tests/test_security_budget.py), [`test_pre_inference.py`](../backend/tests/test_pre_inference.py) | Corte financeiro no provedor: C2/[HOR-026](https://github.com/yannbastos/horus-management/issues/28) |
| R: ninguém sabe o que foi enviado | Registro append-only de transmissões (hash e tamanho, sem conteúdo) | branch retida `feat-hor-115`, ainda **não** na `main` | C5 até integrar |
| E: gateway sem isolamento | Loopback, token, gate sem blockers | [`test_security.py`](../backend/tests/test_security.py) | Sem confinamento de SO: C1; modos Local/Controlado/Conectado: C6/[HOR-113](https://github.com/yannbastos/horus-management/issues/101) |

### 3.4 Exportação e publicação manual

| Ameaça | Controle existente | Prova | Lacuna / Issue |
|---|---|---|---|
| T: ZIP slip, link simbólico, troca do vídeo entre verificação e cópia | Nomes fixos e relativos, links recusados, hash conferido durante a cópia, falha registrada por chave | [`test_exports_adversarial.py`](../backend/tests/test_exports_adversarial.py), [`test_exports_low.py`](../backend/tests/test_exports_low.py) | — |
| I: caminho absoluto ou credencial no pacote | Trava de roteiro/legendas e redação de metadados | [`test_exports_adversarial.py`](../backend/tests/test_exports_adversarial.py) | Caminho POSIX de um segmento fora das raízes comuns não é detectado (baixa) |
| E/R: publicar sem decisão humana | `publishable=false`, aprovação por hash, licenças verificadas por humano, divulgações obrigatórias não removíveis | [`test_publishing_adversarial.py`](../backend/tests/test_publishing_adversarial.py), [`test_license_reviews.py`](../backend/tests/test_license_reviews.py) | Publicação automática não existe por desenho |

### 3.5 Credenciais

| Ameaça | Controle existente | Prova | Lacuna / Issue |
|---|---|---|---|
| I: chave no repositório, log, frontend ou subprocesso | Token só em memória/cabeçalho; chave Hermes em arquivo local; ElevenLabs no keyring e desligado; ambiente dos subprocessos sem credenciais | [`test_job_environment.py`](../backend/tests/test_job_environment.py), [`test_narration.py`](../backend/tests/test_narration.py) | Arquivo de conexão sem ACL auditada nem cifragem: C7/[HOR-114](https://github.com/yannbastos/horus-management/issues/102) |
| S/E: reuso de credencial por outro serviço | Exigência do checklist: chave dedicada ao Hórus; o app não guarda chaves de provedor de modelo | — (procedimento do operador, sem teste) | Teto monetário no provedor (C2) é decisão do idealizador |

## 4. Como manter

Fluxo novo: classe nesta política + linha no inventário + `ALLOWLIST` do teste, no mesmo commit. Controle novo ou lacuna fechada: atualizar a tabela da seção 3 com o teste que o prova. Mudança de classe de um destino (por exemplo, S-1/S-3) só com decisão registrada do idealizador.
