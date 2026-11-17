# Handoff temporário — HOR-163 e HOR-164 (17/11/2026)

DOCUMENTO TEMPORÁRIO para retomar em outra máquina. Apague a pasta `docs/handoff-temporario-2026-11-17/` quando o trabalho voltar à `main`. Governança: `AGENTS.md` e `docs/AGENT-GOVERNANCE.md` valem integralmente (nada de inferência real, serviços pagos, credenciais, publicação ou Git destrutivo sem autorização específica do usuário).

Regras do usuário em vigor: não tocar o banco SQLite operacional nem a instalação real; não migrar dados reais; sem fallback silencioso para SQLite; usar por padrão Claude Sonnet (esforço médio) porque as cotas de Codex e Gemini estão ~70% gastas; ao terminar o processo inteiro, pausar e resumir.

## Onde está cada coisa

| Item | Local |
|---|---|
| `main` (verde, no GitHub) | `d06adcf`. Contém HOR-163 integrado, harness PG (I0), camada `database/` (I1), schema (I2), UI de conexão (M2), `psycopg` no runtime |
| Branch de integração PostgreSQL (INCOMPLETA) | `yannbastos/pg-integration` = `main` + I3 jobs/storage + I4 workflows/gates + I5 produção/roteiros/licenças + I6 Radar + I7 editorial + I8 backup/exclusão + I-mig (ferramenta de migração) + I-final A (corte do runtime + gate anti-SQLite) + Portes B, C, D de testes + I-UI (textos) |
| Porte A (WIP, NÃO integrado) | branch `yannbastos/sb-porte-a` (commit `c97cb64`) — 14 arquivos portados, bateria completa não reexecutada |
| Relatórios dos workers | `docs/handoff-temporario-2026-11-17/reports/` (supabase, release, features) |
| Especificações enviadas aos workers | `docs/handoff-temporario-2026-11-17/specs/` |

A `main` NÃO recebeu o corte para PostgreSQL de propósito: com o corte, `api.py` só funciona com PostgreSQL e ~560 testes legados ainda dependem de portes. Não faça merge da integração na `main` antes da seção "Para fechar o HOR-164".

## HOR-164 — migração SQLite → Supabase PostgreSQL

Decisão definitiva do usuário: 100% Supabase, sem fallback, sem arquitetura híbrida final. FastAPI continua backend; o frontend não recebe credenciais privilegiadas. Tag de recuperação do código legado: `legacy/sqlite-persistence` (main `0a1e4ce`).

### Estado dos testes na integração (último conhecido)
- Grupo B: 298 passed, 2 xfailed (defeitos abaixo). Grupo C: 392 passed, 3 failed (defeitos abaixo). Grupo D: 80 passed, 2 skipped, 1 xfailed. Grupo A: WIP, não medido.
- Transversais nos grupos: 104 passed, 4 skipped. Suíte legada completa NÃO foi executada após os portes.

### Defeitos de produto revelados pelos portes (ABERTOS, nenhum corrigido)
1. `exports.py:~229`: `_INPUTS` foi redefinido como `^inputs/[A-Za-z0-9_.-]+$`; no legado a regra era `PILOT_INPUT_PATH` (cena/áudio fora de `inputs/` contam na proveniência efetiva). Testes: `test_exports.py::test_export_approved_package_hashes_and_effective_licenses`, `::test_font_source_path_in_provenance_does_not_block_export`.
2. `publishing.py` (thumbnail): o legado conferia o hash do vídeo aprovado (`_verify_video`, fora da transação) também no replay → 409 se `video.mp4` sumiu; a versão PG devolve 200. Teste: `test_publishing_adversarial.py::test_publishing_routes_follow_the_current_approval_state`.
3. `GET /v1/imports/{id}` após exclusão devolve 409 (`import_missing_file`) em vez de 404. Causa provável: o registro vivia no arquivo da pasta da importação (sumia com a pasta); em PG a linha `horus.imports` permanece. Teste: `test_data_deletion.py::test_imports_can_be_deleted_hidden_and_restored` (xfail strict).
4. Shutdown: `database/router.py` fecha o pool (`lifespan`) antes do `finally` de `api.py` (`manager.close()`), então a task em execução tenta gravar `interrupted` com o pool fechado. Teste: `test_workflows.py::test_second_backend_cannot_recover_another_workers_running_step` (xfail strict).
5. `POST /v1/jobs` aceita NUL (`\x00`) e `Store.create_job` falha com `UntranslatableCharacter` ao gravar `jsonb`; deveria responder 422. Teste: `test_adversarial_battery.py::test_hostile_file_names_are_refused_by_every_client_path_endpoint[a\x00.mp4]` (xfail strict).

NOTA: tentei corrigir 1–3 no fim da sessão, mas as correções NÃO foram verificadas (a seleção de testes de exports/publicação passou a ter 13 falhas, o teste de imports continuou falhando) e foram REVERTIDAS (`ff2b378`). Investigue com calma: o export falha com 409 "Não foi possível exportar com integridade" já na criação; compare `exports.py`/`publishing.py`/`project_import.py` da integração com `origin/main` e com os testes legados. Ao corrigir, remova os `xfail(strict=True)` correspondentes.

### Para fechar o HOR-164
1. Terminar o Porte A (`sb-porte-a`): rodar os 14 arquivos, fechar `reports/supabase/Porte-A.md`, depois `git merge` na integração (resolver `conftest`/`support_*`, se houver conflito).
2. Corrigir os 5 defeitos acima (testes primeiro) e remover os xfails.
3. Rodar a suíte backend COMPLETA na integração (`backend/.venv\Scripts\python.exe -m pytest backend/tests contracts/hermes/tests -q`, ~13+ min; processos separados para os transversais `test_supply_chain`, `test_rust_supply_chain`, `test_network_inventory`, `test_api_contract_snapshot`, `test_endpoint_auth*`), mais `npm --prefix frontend run build` e `npm --prefix frontend test`. O gate `backend/tests/test_no_hidden_sqlite.py` e a jornada `backend/tests/pg/test_journey_pg.py` devem ficar verdes.
4. Revisão independente final de toda a branch (Antigravity ou Claude), foco: segurança preservada (aprovação humana, orçamento, consumo único, append-only, hashes), nenhum SQLite fora da allowlist, sem segredo em log.
5. Só então: merge na `main`, push, atualizar Issues #151/#150 e o Project, limpar worktrees/branches (`git branch -d`, sem `-D`/force).
6. Ainda NÃO feito: validação real contra Supabase (TLS positivo com a CA embarcada, Supavisor 5432); suporte a destino remoto na ferramenta de migração (hoje só PostgreSQL local descartável); PG17 oficial (download EDB falhou com HTTP 500; harness roda em PG16.2 via `pgserver`); supersede de `docs/adr-persistencia-local-remota.md`; textos de AGENTS/governança "Local First" (depende de OK do usuário); migração dos dados reais (somente com autorização específica; procedimento em `docs/procedimento-migracao-sqlite-postgres.md`).

### Decisões pendentes do usuário
- Criar projeto Supabase de desenvolvimento (plano gratuito, região `sa-east-1`) e rodar `npx supabase login`; informar só "feito", nome e região. O plano Free pausa após 7 dias sem uso e NÃO tem backups automáticos: produção provavelmente exige Pro (decisão financeira do usuário).
- OK para ajustar a redação "Local First"/offline em AGENTS/governança (o app fica inutilizável sem rede e o conteúdo privado passa a viver na nuvem de terceiro).
- RPO/RTO e destino dos backups; teto de checkpoints (HOR-161).

## HOR-163 — instalador NSIS e canal de atualização Cloudflare

Integrado na `main` (`6d6c17a`): Worker + R2 (`infra/cloudflare`), pipeline `.github/workflows/release.yml` + `scripts/release`, atualizador Rust autenticado, UI de atualização. NADA foi executado na nuvem. Ver o comentário na Issue #150 e `reports/release/`.

Falta (depende do usuário): escolha Cloudflare A (cartão para R2, com alerta de orçamento) ou B (Releases privados + Worker/KV, sem cartão); checklist de 10 passos em `reports/release/E-cloudflare-prereqs.md`; custódia da chave de PRODUÇÃO; Environments/secrets/variáveis no GitHub (`UPDATE_TEST_UPDATER_PUBKEY`, `UPDATE_PRODUCTION_UPDATER_PUBKEY`, `HORUS_CI_READ_TOKEN`, `R2_*`, `TAURI_SIGNING_*`); testes reais de instalação/atualização (sem admin: sem Hyper-V/Sandbox; ensaio em identidade isolada HOR-147); WebView2 ausente (só em máquina real sem WebView2); pacote de recursos grandes e perfil Primeiro vídeo no instalador; primeira release de teste.

## Ferramentas e armadilhas conhecidas
- Orca: `orca orchestration worker-start --spec-file <f> --agent claude --model claude-sonnet-5-5 --effort medium|antigravity --model gemini-4-hydrogen-medium --repo id:9f5fff79-f9fa-497f-bc00-9d733f13bd05 --worktree new-top-level --base-branch yannbastos/pg-integration`.
- Ambiente: `backend/.venv` via `uv sync --project backend --frozen --all-extras` (sem os extras faltam yt-dlp/pyinstaller etc.). Testes PG usam `pgserver` (PG16.2) em TEMP descartável, porta loopback.
- Testes Rust: `cargo test --locked --offline` com `TAURI_CONFIG='{"bundle":{"resources":[]}}'` (ver `docs/auditoria-rust-tauri.md`).
- Máquina da WEG: TLS inspecionado (CA corporativa); sem admin; sem Docker.
- Não toque `%LOCALAPPDATA%\com.horus.audiovisual`, `%LOCALAPPDATA%\Horus`, `Programs\Horus` (dados/instalação reais).
