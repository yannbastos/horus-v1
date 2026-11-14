# Plano: Hermes real em pesquisa, propostas e roteiros

Estado em 14/11/2026, sobre `main` `bb2b8f0`. **Este documento só planeja; não autoriza nem habilita inferência.** Toda etapa marcada com 🔒 depende de autorização específica do idealizador (ver `AGENTS.md` e [checklist-pre-inferencia.md](checklist-pre-inferencia.md)).

## 1. Onde estamos

| Peça | Estado | Evidência |
|---|---|---|
| Adaptador Hermes (diagnóstico, `/v1/hermes/probe`) | Pronto, só loopback | [integracao-hermes.md](integracao-hermes.md) |
| Gate de segurança (`/v1/security/status`, HTTP 409 `security_gate`) | Pronto; bloqueia sem perfil, preços e tetos | `test_pre_inference.py`, `test_security*.py` |
| Autorização humana por execução, idempotência, reconciliação | Pronto | `workflow_hermes.py`, `test_workflow_hermes.py` |
| Etapas cognitivas `planning`/`research`/`analysis`/`proposal` | Prontas contra servidor falso; padrão `simulated` | `contracts/hermes/`, `HORUS_WORKFLOW_EXECUTOR` |
| Pesquisa (Radar) | Snapshot local; coleta YouTube real bloqueada (HOR-039) | `HORUS_RADAR_EXECUTOR=hermes_simulated` |
| **Roteiro** | Só simulado (`simulate_script`) ou escrito pelo usuário (HOR-133); **não há etapa Hermes** | `scripts.py` |
| CLI Hermes neste PC | Instalado (`%LOCALAPPDATA%/hermes/bin`) | verificação de existência, sem execução |
| Perfil `horus` neste PC | **Ausente** (o endurecimento relatado em outro momento não está presente aqui) | diretório `profiles/` inexistente |
| Arquivo de conexão Hórus→Hermes | **Ausente** | `Horus/workspace/integrations/` inexistente |
| Checklist A (offline) | **Verde em 14/11/2026**: 212 passed, 1 skipped (symlink); contratos 57 passed | rodado pelo Maestro na `main` `bb2b8f0` |

## 2. Objetivo da fase

Pesquisa → proposta → roteiro com Hermes real, mantendo:
- aprovação humana em cada etapa (nada vira produção sem revisão);
- custo previsível com teto no provedor e no Hórus;
- zero ferramentas no perfil, loopback, sem memória;
- roteiro gerado validado pelo mesmo `ScriptDocument` do roteiro manual, com `origin` distinto (`hermes`) e o mesmo ciclo de revisão;
- fallback sempre disponível: simulado e escrito pelo usuário.

## 3. Fases

### F0 — Preparação sem inferência (pode começar já, sem autorização)
1. **Contrato da etapa de roteiro** (`contracts/hermes`): esquema de saída `script` → `ScriptDocument`, fixtures fictícias e testes no servidor falso. Saída inválida = rejeitada, nunca "consertada".
2. **Etapa `script` no backend** atrás do executor (`HORUS_WORKFLOW_EXECUTOR=simulated` continua padrão): pede autorização humana, reserva orçamento e registra `cognitive_runs`, exatamente como `proposal`. Origem `hermes` só aditiva (migração aditiva, se necessária, revisada pelo Maestro).
3. **Envelope mínimo de dados**: o que vai ao Hermes (proposta aprovada, fontes permitidas, restrições editoriais) e o que nunca vai (caminhos locais, credenciais, outros projetos). Teste de que o envelope não carrega campos extras.
4. **UI de autorização**: antes de cada execução real, a tela mostra destino, modelo, estimativa de tokens/custo, dados enviados e botão explícito; o resultado aparece como "Gerado pelo Hermes" no histórico.
5. **Relatório B automatizado**: comando que consulta `/v1/security/status` de um backend já em execução e imprime B1–B9 sem expor o token.

### F1 — Decisões do idealizador 🔒
- Provedor e modelo; preço de entrada/saída por token (sem tabela embutida).
- Chave dedicada com **teto monetário configurado no provedor** (C2).
- Política de retenção/treinamento do provedor revisada (C8).
- Aceite escrito ou mitigação de C1 (sem isolamento de SO), C5 (sem auditoria de transmissões) e C6 (sem modos de privacidade).
- Tetos do Hórus: padrão aprovado 16384 tokens/execução, 32768/dia, 2 execuções/dia, USD 5 total, USD 1/dia.

### F2 — Ambiente Hermes 🔒
`scripts/Prepare-Hermes.ps1` (cria perfil `horus` e chave local), `scripts/harden_hermes_profile.py --dry-run` e depois `--allow-real`, conexão do provedor no perfil. Repetir checklist A e B no perfil real; B1–B9 verdes antes de seguir.

### F3 — Primeira execução real controlada 🔒 (HOR-027 #29)
Dataset **fictício**, uma execução de `analysis`, teto mínimo. Conciliar `usage` × `reserved_usd`; divergência fecha o gate.

### F4 — Proposta real 🔒 (HOR-047 #49)
Proposta a partir de um snapshot local real do Radar. Revisão humana obrigatória.

### F5 — Roteiro real 🔒 (HOR-134)
Roteiro gerado de uma proposta aprovada → revisão/edição humana (editor HOR-133) → produção local existente.

### F6 — Coleta externa do Radar 🔒 (HOR-039 #41)
Separada do Hermes; exige autorização própria (YouTube API).

## 4. Riscos principais
- Gate local aberto ≠ segurança (R1 do checklist): C1, C2, C5 e C6 continuam abertos.
- Estimativa ≠ cobrança (R2): o teto real tem que estar no provedor.
- O perfil pode recriar skills/cron ao reiniciar (R3): o gate fecha, mas exige revisão.
- Roteiro gerado pode citar fatos sem fonte: só fontes do snapshot aprovado são aceitas; a revisão humana continua obrigatória.

## 5. Issues
- F0: HOR-134 #121 (roteiro via Hermes), HOR-112 #100 (perfil), HOR-110 #98 (inventário de fluxos).
- F1–F2: HOR-026 #28.
- F3: HOR-027 #29. F4: HOR-047 #49. F6: HOR-039 #41.

## 6. Requisitos de segurança: o que existe na `main` e o que falta (revisão de 14/11/2026)

Revisão deste plano e de [politica-destinos-e-ameacas.md](politica-destinos-e-ameacas.md) contra o código da `main` `cd365b1`. Não habilita inferência. Linhas marcadas **branch** estão em branches retidas da linha Hermes e **não foram editadas** nesta revisão.

| Barreira | Na `main` | Lacuna | Prova / destino |
|---|---|---|---|
| Orçamento | Gate `security_gate` (409), tetos por execução/dia, reserva antes do envio, sem retentativa automática | Teto monetário real no provedor (C2) é decisão do idealizador; conciliação `usage` × reserva só em F3 | `test_security_budget.py`, `test_pre_inference.py` |
| Allowlist de destinos | `HermesRunClient`/`validate_origin`: só `http` em `127.0.0.1`/`::1`, sem proxy e sem redirect; caminhos `/v1/runs…` fixos | O Hórus **não controla nem audita o provedor/modelo** para o qual o Hermes encaminha (C-Hermes); allowlist de provedor/modelo só existe como configuração do perfil Hermes (C1/C6) | `test_hermes.py`, `test_workflow_hermes.py`; política §2 |
| Redação de dados privados | Envelope de **roteiro** (`ScriptRunEnvelope`) rejeita caminho local e credencial (`_FORBIDDEN_TEXT`); ambos os envelopes recusam campos extras e IDs com caminho | **Envelope de análise (`RunEnvelope`) não rejeita caminho/credencial em texto livre** (`objective`, `feedback`, `channel_profile.summary`); confirmado: `C:/Users/…`, `/home/…`, `api_key = …` e `Bearer …` são aceitos | `contracts/hermes/tests/test_envelope_privacy.py` (`xfail(strict)` documenta; corrigir em `hermes_contract/models.py`, fora do escopo desta entrega, e então remover a marcação) |
| Aprovação humana | Autorização por execução, idempotência, reconciliação; aprovação humana de proposta e de roteiro | Etapa `script` ainda não existe no backend (só contrato e servidor falso); a autorização exibida ao usuário (destino/modelo/estimativa/dados) é F0.4 | `workflow_hermes.py`; F0 |
| Auditoria | `cognitive_runs` e eventos de workflow; saída validada por esquema, fontes e `task_id` | Registro append-only de **transmissões** (hash/tamanho do que sai) — **branch** `feat-hor-115` (C5/HOR-115), ainda não integrado; nada a editar aqui | política §3.3 |
| Injeção/saída | Perfil sem ferramentas auditado; fontes como dado; saída inválida rejeitada, nunca “consertada” | Regressões com modelo real só após autorização (C9) | `test_security_profile.py`, `contracts/hermes/tests/test_script_stage.py` |

Observação de estado: a tabela da seção 1 e a F0.1 descrevem o contrato da etapa `script` como pendente, mas `hermes_contract/script.py` e `contracts/hermes/tests/test_script_stage.py` já existem na `main`; falta a etapa no backend (rotas/workflow — **branch** `feat-hor-134*`, não tocada).

Ordem sugerida para fechar a lacuna de redação (antes de qualquer F3): aplicar `_FORBIDDEN_TEXT` aos textos livres de `RunEnvelope`, remover o `xfail` e acrescentar o mesmo teste ao envelope de `script` integrado.
