Histórico preservado em 17/11/2026 a partir da branch `yannbastos/tmp-lote-descartada` (commit d5d3d34e1c4ee753462449c00d6e448d9960644a); NÃO é instrução vigente; sanitizado (ver lista no commit)

# Inventário de branches pendentes — backend/integração (16/11/2026)

Base: `origin/main` = 65fffbf. Análise sem merge/push na main; merges de teste só em branches locais descartáveis. `HORUS_DATA_DIR` temporário em todos os testes. Backend na porta 8765 não tocado.

## Tabela

| Branch | Issue / PR | Commits exclusivos | Integrada? | Conflitos com a main | Testes | Classificação | Recomendação |
|---|---|---|---|---|---|---|---|
| `yannbastos/feat-hor-138-whisper-download` | #125 (HOR-138) · PR #3 aberta, CONFLICTING | 1: `086b102` download explícito e verificado de modelos Whisper (10 arquivos, +682) | Não (`cherry`: 1 exclusivo) | 1 trivial: imports em `api.py` (main trouxe `ManualWorkflowRequest`; PR traz `whisper_download`). Resolvido mantendo ambos no merge de teste | Focados + transversais: **134 passed, 5 skipped**. Snapshot OpenAPI: **+202/−0, aditivo**. Suíte completa iniciada: 73 % sem falhas, mas excedeu 580 s e foi interrompida | **INTEGRÁVEL** (após resolver o import em `api.py`) | Maestro: merge da main na branch da PR, suíte completa uma vez, integrar. Pendências menores: docstring de `whisper_download_catalog.py` com `?` no lugar de acentos (byte `3f` no arquivo); o download não passa pelo gateway de saída (HOR-111, só na v3) — a integração da v3 deve reconciliar a allowlist; ensaio real contra os CDNs do Hugging Face segue pendente (documentado na própria PR) |
| `yannbastos/feat-hor-139-phase-b` | #126 (HOR-139) · PR #4 aberta | 3: `16614af` docs; `f3f2443` schema remoto + prova RLS; `cb447ef` contratos locais desativados (11 arq., +729) | Não | **Nenhum** (merge automático) | Focados + transversais: **112 passed, 4 skipped**. Snapshot OpenAPI inalterado (sem endpoints). `supabase/tests/*.sql` não executados (exigem banco; acesso remoto proibido) | **INTEGRÁVEL** | Migração: `ENABLE`+`FORCE RLS`, `REVOKE` para `anon/authenticated`/`PUBLIC`, sem segredos no diff (o ref `horus-dev` só aparece em docs). A doc afirma migração já aplicada no `horus-dev` (não verificado: sem acesso remoto). Conferir que os ajustes da revisão de segurança (comentário 6894844016 da #126) estão refletidos |
| `checkpoint-2026-11-16` | — | 0 (aponta 2852f96, 14 commits atrás da main) | Sim (ancestral) | — | — | **PRESERVADA INTENCIONALMENTE** (marco da troca de computador, RETOMADA-MAESTRO §I) | Nenhum documento a referencia pelo nome. Manter até a retomada no outro PC; depois excluir ou virar tag |
| `build/hor-100-windows-codex` | PR #5 (mesclada); HOR-100 #88 / HOR-006 #2 | 0 | Sim (ancestral) | — | — | **OBSOLETA** (conteúdo na main) | Único uso é textual em `docs/validacao-instalador-codex-2026-11-15.md` (nome do checkout; não depende da branch). Pode excluir (integrada, sem PR aberto) |
| `review/m3-scripts` | #51 HOR-049 (fechada), #120 | 1: `26fa36e` snapshot "preserva estado local" (18 arq., +2942) | Não por cherry; base de 12/11, 403 commits atrás | — (não testado; arquivos divergiram muito) | — | **OBSOLETA** (alta confiança; prova parcial) | 8 de 18 arquivos idênticos à main; nos demais a main tem a versão evoluída; linhas ausentes na main são iterações anteriores. Prova linha a linha não feita em `.py`/`.tsx` |
| `review/m4-production` | #54 HOR-052 (fechada) | 1: `cbb43fa` snapshot (14 arq., +4192) | Não por cherry; 393 atrás | idem | — | **OBSOLETA** (alta confiança; prova parcial) | Contrato/docs idênticos; worker, produção e UI evoluídos na main |
| `review/parallel-round` | — (config de worktree, privacidade) | 1: `fbca066` snapshot (23 arq.) | Não por cherry; 412 atrás | idem | — | **OBSOLETA** (provada) | 17 de 23 arquivos idênticos à main; nos 6 restantes **todas** as linhas adicionadas pela branch existem na main |
| `review/pilot-local` | piloto local (HOR-100/#130 contexto) | 1: `9a47bc7` snapshot (16 arq., +4331) | Não por cherry; 384 atrás | idem | — | **OBSOLETA** (alta confiança; prova parcial) | Resíduos = iterações anteriores de `local_pilot*.py`, `ProductionRun.tsx`, `production.ts` |
| `yannbastos/feat-hor-049-scripts-backend` | #51 HOR-049 (fechada) | 1: `d4afe1a` snapshot (`script_contract.py`, +84) | Não por cherry | — | — | **OBSOLETA** | A main tem versão posterior (`simulated` deixou de ser `Literal[True]`) |
| `yannbastos/feat-hor-052-production-backend` | #54 HOR-052 (fechada) | 1: `c02fe17` snapshot (`production_contract.py`, +175) | Não por cherry | — | — | **OBSOLETA** | A main tem versão posterior (`mode`/`local_demo`) |
| `integ-hermes-line-v3` | #121 #103 #99 #95 #117 | 24 sobre a main atual (13 por cherry; base 1fa3c7f de 14/11, **126 commits atrás**) | Não | **8 arquivos**: `api.py`, `radar_youtube/transport.py`, `snapshots/openapi.json`, `snapshots/first-video-shapes.json`, `docs/RETOMADA-MAESTRO.md`, `EditorialWorkflows.tsx`, `services/api.ts`, `styles/app.css` | Não rodados (somente análise) | **PRECISA CORREÇÃO** (merge da main + regerar snapshots) e **PRESERVADA INTENCIONALMENTE** (só com autorização específica, §F.1/§I) | Maestro: `git merge origin/main` na v3, resolver os 8 conflitos (snapshots: regerar com `HORUS_UPDATE_API_SNAPSHOT=1` e conferir só `+`), suíte completa, migração v9→v12 em cópia, Playwright |
| `yannbastos/integ-hermes-line-v2` | #121 #103 | — | Contida na v3 (ancestral) | — | — | **PRESERVADA** (componente) | Não integrar separado |
| `integ-hermes-line` (v1) | — | — | Não ancestral da v3, mas `git cherry v3` = 0 (patches equivalentes) | — | — | **OBSOLETA** (substituída por v2/v3) | Pode excluir após a v3 ser integrada |
| `feat-hor-111-egress-gateway` | #99 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `feat-hor-107-human-checkpoints` | #95 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `feat-hor-115-transmission-audit` | #103 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `test-hor-130-adversarial` | #117 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `yannbastos/feat-hor-107-checkpoints-ui` | #95 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `yannbastos/feat-hor-115-transmissions-ui` | #103 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `yannbastos/feat-hor-134-script-stage` | #121 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |
| `yannbastos/fix-hor-134-script-stage-review` | #121 | — | Contida na v3 | — | — | **PRESERVADA** (componente) | Aguardar a v3 |

Contenções: `git merge-base --is-ancestor origin/<b> origin/integ-hermes-line-v3` verdadeiro para todos os componentes exceto `integ-hermes-line` (v1); `git cherry origin/integ-hermes-line-v3 origin/<b>` = 0 `+` em todos.

## Comandos de teste (PR #3 e #4)

```powershell
uv sync --project backend --frozen --extra test      # sem erro de TLS
git switch -c tmp-analise-pr3 origin/yannbastos/feat-hor-138-whisper-download
git merge --no-ff --no-commit origin/main            # conflito só em api.py (imports); resolvido mantendo ambos
$env:HORUS_DATA_DIR = <pasta temporária>
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_whisper_download.py backend/tests/test_whisper_models.py backend/tests/test_network_inventory.py backend/tests/test_api_contract_snapshot.py backend/tests/test_supply_chain*.py backend/tests/test_endpoint_auth*.py -q   # 134 passed, 5 skipped
# PR #4: merge limpo; test_persistence_contracts.py no lugar dos de Whisper e mesmos transversais   # 112 passed, 4 skipped
```

Nenhuma correção foi commitada nas branches das PRs e não houve push.

## Resíduos locais desta análise

- Worktree `analise-branches-backend` em HEAD destacado na main; branch local `tmp-analise-pr4` (merge de teste, não publicada) fica para exclusão pelo Maestro.
- Usei `git branch -D` em duas branches locais descartáveis minhas (`tmp-analise-v3`, sem commits próprios; `tmp-analise-pr3`, só o commit de merge de teste). Nada publicado ou de terceiros foi perdido.

## Decisões para o usuário

1. Excluir as 6 branches OBSOLETAS (`review/*`, `feat-hor-049/052`): não estão integradas por commit, então exige autorização; a prova arquivo a arquivo é total só em `review/parallel-round`.
2. `checkpoint-2026-11-16`: manter ou converter em tag após a troca de computador.
3. Autorização específica para integrar a v3 (8 conflitos, 126 commits de defasagem).
