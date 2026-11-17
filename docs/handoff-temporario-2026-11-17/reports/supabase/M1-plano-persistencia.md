# HOR-164 / M1 — persistência exclusiva PostgreSQL: proposta

17/11/2026 · Fase 1: leitura do repositório + prova sintética local · base `0a1e4cea951fdbf5bf8e64d2ee9d10a42e410e94`.
**Proposta para revisão do Maestro; nenhum código de produto, banco real, instalação ou recurso Supabase alterado.**

## 1. Evidência e divergências

| Classificação | Resultado |
|---|---|
| Verificado | Git limpo na entrada; issue #151 lida via gh; migrations.py define **v12**, com **17 tabelas principais + 4 Radar = 21 ativas**; journal opcional acrescenta **3** |
| Módulos | **8** arquivos de produção importam sqlite3; **24** executam SQL/abrem banco/delegam acesso por store.connect ou inicialização; worker_lock.py é dependência adicional de coordenação |
| Busca exigida | **82 arquivos**: 21 produção backend + measure_checkpoints.py, 48 testes/apoios, 10 scripts, 2 Tauri (inclui Cargo.lock); zero contracts/. Regex ampla tem falsos positivos como os.walk; 82 não significa 82 módulos SQL |
| Testes | 1.308 definições test_ estáticas; ≈2,9 mil casos parametrizados é informação do despacho, **não recontada por pytest**; 53 arquivos de teste referenciam Store/Radar/SQLite/conexão |
| Ambiente | Este worktree **não tem backend/.venv**; Python padrão 3.14.7; Python 3.12.14 já disponível via uv; criado venv exclusivo nesta pasta de relatório |
| Artefatos de evidência | M1-rg-sqlite.txt: busca completa; M1-sql-functions.txt: índice AST auxiliar (23 módulos, não detecta delegação em narration/service.py); M1-probe-pg.py, M1-pg-local.log, M1-pg-local-result.json: prova local |
| Recebido, não repetido | Sem admin/Docker; CLI Supabase 3.2.0 sem login; HTTPS com CA WEG; pooler IPv4 5432/6543 alcançável, TLS PG5432 não interceptado; direto IPv6 inalcançável. Nenhuma conexão remota nesta sessão |

**Divergência documental explícita:** ARCHITECTURE ainda descreve SQLite inicial e funções essenciais offline; seguranca-persistencia-remota rejeitava senha de BD no backend desktop e exigia Auth individual. A decisão definitiva da issue supera o ADR híbrido e autoriza propor FastAPI local com papel mínimo no cofre; não transforma esse processo em servidor confiável contra malware do mesmo usuário. Atualizar contratos em incremento próprio, sem anunciar isolamento multiusuário inexistente.
Reaproveitar HOR-137/139: inventário, allowlists, testes negativos de grants/RLS, idempotência, schema privado e proteção de segredos. Não reaproveitar outbox híbrida nem criar Edge Function nesta migração.

## 2. Mapa real por módulo

Caminhos relativos a backend/src/horus_backend/. Referências completas de funções/linhas no índice auxiliar; wiring e dependências indiretas explicitados aqui.

| Arquivo / funções representativas | Tabelas, uso real e consequência |
|---|---|
| migrations.py:migrate | DDL v1–v12, user_version, sqlite_master, FK/WAL, BEGIN IMMEDIATE e backup pré-migração; não portar ALTER/DROP SQLite para PG |
| storage.py:Store.__init__,connect,checkpoint | horus.sqlite3; identidade por path/inode, sqlite3.Row, secure_delete/FK, checkpoint WAL/TRUNCATE, freelist/page_count; trocar identidade por registro remoto estável |
| storage.py:create_job,update,event,recover,register_media | jobs/events/media; idempotência por JSON serializado, last_insert_rowid, transcrição texto+evento atômicos, poda 2.000 eventos/job e upsert por path |
| api.py:create_app,update_activity,update_health,update_backup,radar_demo,download_whisper_model | Instancia Store/Radar (migra/purga), consulta jobs, PRAGMA integridade/versão e exceções sqlite; novo wiring PG preserva auth loopback |
| jobs.py:JobManager.start,_retention_cycle | Recuperação/loops de jobs e fila; reconstrói Radar por store.path.parent; separar data_dir da identidade do banco |
| workflows.py:get,create*,_claim_tx,_finish,recover,cancel,retry,authorize,reconcile,review | workflows/jobs/job_attempts/artifacts/approvals/workflow_events/cognitive_runs/authorizations; dez BEGIN IMMEDIATE; revisão, etapas, autorização e recibos atômicos |
| workflow_hermes.py:_record,analysis | cognitive_runs; intenção durável antes da rede, external_ref/idempotência/unknown; não repetir envio sem reconciliação |
| scripts.py:create,create_hermes,reconcile_hermes,new_version,review | scripts/script_receipts + approvals/artifacts/jobs/job_attempts/authorizations/cognitive_runs; versão/revisão/recibos e comparação canônica |
| production.py:create,review,run,list,record | productions/production_receipts/jobs/scripts/script_receipts; snapshot roteiro/revisão, plano/artefatos, cancelamento/commit final e arquivos externos à transação |
| publishing.py:approved_script,_approved,_append,_extract,_metadata,edit_metadata | productions/jobs/scripts/script_receipts/artifacts; thumbnail/metadados, revisões e recibos JSON em disco; não renderizar/rede sob lock PG |
| exports.py:_create; narration/service.py:estimate | Export consulta jobs/script_receipts e produção; narração delega ProductionService.approved; ambos dependem do Store, mesmo sem import sqlite3 |
| license_reviews.py:_provenance,_history,create | productions/license_reviews; append-only, supersedes por asset, hash/recibo e BEGIN IMMEDIATE |
| human_checkpoints.py:grant,consume,page | human_checkpoints; expiração/limite/moeda/subject e consumo único; recebe conexão da UoW chamadora |
| transmissions.py:TransmissionLog.record,page,best_effort | transmissions/authorizations; tamanho/hash do envelope sem conteúdo; intenção obrigatória deve falhar fechada se BD cair |
| security.py:budget_usage,daily_tokens,daily_runs | cognitive_runs JOIN jobs/workflows; orçamento agregado antes da intenção; precisa lock por escopo para impedir overspend concorrente |
| retention.py:run_retention,_redact_* | workflows/artifacts/cognitive_runs/jobs/job_attempts/workflow_events/scripts/script_receipts; redação Radar por workflow e checkpoint WAL; PG não oferece secure_delete equivalente |
| error_center.py:_all | jobs LEFT JOIN productions e workflows; JSON operation, datas com sentinelas ''/'9999'; substituir por filtros opcionais tipados |
| notifications.py:_events | Deriva notificações de jobs/productions/scripts/workflows; lido/não lido em JSON, sem tabela hoje |
| data_deletion.py:_active_jobs,_db_kept,_backup,execute,restore | jobs/productions/events/job_attempts/production_receipts/license_reviews/scripts; preserva linhas e move arquivos à quarentena após backup; auditoria JSON encadeada |
| backups.py:inventory,plan,remove_backups,_radar_counts,_inspect,verify,restore | horus.backup-v*, PRAGMA integridade/versão, contagem Radar JSON, ro/immutable e restore por SQLite backup API para destino novo |
| local_data_export.py:_snapshot,export_local_data,verify_export,restore_local_data | ZIP com snapshots principal/Radar, allowlist/hash/manifest, restore em pasta nova e validação de versão; update_backup.py delega, sem SQL próprio |
| radar_youtube/state.py:RadarState.* | DDL no construtor, radar_cache/usage/blocks/datasets, purge em leituras, INSERT OR REPLACE, epoch REAL, julianday(json_extract); sem user_version |
| radar_youtube/state.py:collection_lock; radar_youtube/__main__.py:main | radar-youtube.refresh-lock.sqlite3 **sem tabelas**, BEGIN EXCLUSIVE e espera cancelável; CLI abre Radar separado. Remover ambos os caminhos SQLite |
| persistence_contracts.py:LocalUnitOfWork.transaction,LocalSyncJournal.* | sync_outbox/inbox/cursors criados só com enabled=True; app não instancia; rowid e CAST AS BLOB para quota; eliminar journal híbrido |
| worker_lock.py:WorkerLock.acquire/close (adicional) | horus.sqlite3.worker.lock, msvcrt/flock; exclusão só por arquivo/máquina, insuficiente para dois PCs no mesmo PG |

| Migração SQLite | Efeito |
|---|---|
| v1 | jobs, events, media e events_job |
| v2 | workflows, job_attempts, artifacts, approvals, workflow_events; FKs/etapas em jobs; ciclo workflows↔artifacts |
| v3 / v4 | cognitive_runs, authorizations, executor / cognitive_client |
| v5 / v6 | scripts+script_receipts / productions+production_receipts |
| v7 | Reconstrói productions/receipts para CHECK local_pilot; tabelas _v7 são transitórias |
| v8 / v9 / v10 | license_reviews+2 triggers / scripts.origin / scripts.generator |
| v11 | transmissions, índice run/seq e triggers no_update/no_delete |
| v12 | human_checkpoints, lookup action/subject/seq e triggers no_delete/consume_once |

connect(timeout=5) configura espera ocupada via driver; **nenhum PRAGMA busy_timeout explícito localizado**. migrate usa WAL; Store aplica FK e secure_delete por conexão, Radar secure_delete. Sidecars -wal/-shm/-journal não são bancos adicionais nem backups independentes; nunca copiar principal ativo sem snapshot. executescript aparece como advertência por poder quebrar atomicidade. Nenhuma coluna BLOB no DDL atual: apenas CAST AS BLOB no journal para medir bytes.

| Scripts/testes/Tauri | Uso e destino |
|---|---|
| scripts/ensaio_atualizacao_banco.py | HOR-157 cria v9 sintético, exporta/restaura e migra cópia a v12; legado do importador, não upgrade PG |
| scripts/check-installer-data.py, test-installer-data-isolation.ps1 | Fixtures SQLite/preservação da instalação; manter prova do legado e acrescentar isolamento PG sintético |
| scripts/updater-e2e-support.py, updater-e2e.mjs | Fingerprint, escritor WAL, backup/restore e IDs wal-writer-*; converter ensaio operacional sem impor UUID incompatível |
| scripts/smoke-windows-package.py, measure-local-pilot.py; backend/measure_checkpoints.py | Esperam arquivo SQLite, consultam/medem checkpoints; novo smoke verifica estados PG sem criar BD local |
| scripts/prepare-first-video.py | SQLite na lista de arquivos proibidos do pacote; preservar proibição e acrescentar DSN/segredos |
| scripts/vm/guest-support.ps1, guest.ps1 | P/Invoke winsqlite3.dll, integridade/fixtures da instalação; não é driver Rust |
| frontend/src-tauri/src/updater.rs, Cargo.lock | Updater chama /v1/update/backup e teste menciona path SQLite; regex/Cargo.lock não prova engine SQLite operacional Rust; alinhar com owner HOR-163 |
| backend/tests/ | Store(tmp_path/...), API com data_dir temporário, SQL direto/PRAGMA/locks/crash; fixtures distribuídas. conftest autouse isola **keyring em memória**, não há fixture global única de Store |
| contracts/ | Busca sem ocorrência; contratos Hermes não devem ganhar dependência de BD |

## 3. Arquivos fora do PostgreSQL e JSON operacional

| Área | Destino proposto |
|---|---|
| Mídias originais/vídeo/áudio/imagens, modelos, render/staging, exports/quarentena | **Arquivos de disco, não banco**; permanecem locais; PG guarda metadados/hash/estado, com erro de arquivo indisponível distinto de BD indisponível |
| Planos/manifestos/QC/alinhamento/proveniência/pilot-evidence.json junto ao vídeo | Artefatos verificáveis podem permanecer como arquivos; PG registra identidade/hash, sem usá-los como banco alternativo de jobs/revisões |
| notifications-state.json, production-comments/*.json, narration-preference.json, deletion-audit.json | Não são SQLite, mas são **estado operacional**: proposta conservadora para 100% é migrar a tabelas, sem dupla escrita autoritativa |
| imports/*/import.json, publishing/**/receipt.json e failed.json | Manifestos de pacote ficam como evidência de arquivo; índice de imports, recibos e estado de publicação passam a PG; historical_only jamais autoriza executar |
| Cofre Windows/keyring, config host público, perfil Hermes/segurança, ACL/runtime | Segurança/configuração de máquina fora do PG; nunca enviar credenciais/perfil Hermes; nenhum cofre real lido nesta fase |
| localStorage/UI e recuperação de transcrição | Preferências visuais/dispositivo podem continuar locais; recibo não é banco nem fila. Trocar identidade por database_instance_id; M2 revisa ausência de escrita operacional offline |
| Bancos Hermes/fábrica independentes, inclusive cron/executions.db reconhecido por security.py | Fora do escopo Hórus, sem alteração/migração |

## 4. Schema PostgreSQL proposto

Schema **horus**, distinto de public/auth/**horus_sync**; Data API segue desativada. Nomes, nulabilidade/defaults e CHECKs v12 preservados salvo conversões explícitas; SQL qualificado, sem ORM novo.
**IDs legados ficam text**, preservando literalmente UUID hex/IDs externos, API/paths/hashes; uuid apenas para novas identidades internas. Datas ISO válidas→timestamptz UTC; revisões/attempts→integer; bytes/tokens/seq/modified_ns→bigint; estados→text+CHECK; booleanos novos→boolean. Dinheiro→numeric sem escala fixa após parse Decimal finito/exato, nunca float.
JSON TEXT→jsonb, com validação de tipo. **Hash/idempotência não pode virar hash de jsonb::text**: preservar bytes canônicos originais em *_canonical bytea nos campos hasheados/request/recibos/artifacts/scripts, ou provar canonicalização idêntica antes de remover a cópia. Duplicação no mesmo PG não é fallback. DTO conserva formato HTTP atual; JSONB/datetime/Decimal precisam conversão explícita.

| Tabela ativa (21) | Tipos/dados principais | Chaves/FKs/índices |
|---|---|---|
| jobs | id/key/status/error text; payload/result jsonb; created/updated timestamptz; attempt/revision/step_index integer; refs text | PK id, UQ key, UQ workflow/revision/step_key; FK workflow/depends_on; índices status/created/id e workflow/revision/step_index; operation parcial se medido |
| events | sequence bigint GENERATED ALWAYS AS IDENTITY; job/kind/message text; created timestamptz | PK sequence, FK job; events_job(job_id,sequence) |
| media | id/path/name text, size/modified_ns bigint, created timestamptz | PK id, UQ path no corte estação única; vários PCs exigem device_id+path antes de ampliar escopo |
| workflows | id/key/status/feedback/executor/cognitive_client text; payload jsonb; revision integer; datas; current_artifact text | PK id, UQ key; FK current_artifact adicionada após artifacts, DEFERRABLE para carga; índices created/id e status |
| job_attempts | id/job/executor/input_hash/error text; number integer; input_artifacts/result jsonb; started/finished timestamptz | PK id, UQ job/number, FK job; índice job/started |
| artifacts | id/refs/stage/hash text; revision/schema_version integer; content jsonb+canonical bytea; created timestamptz | PK id, UQ job e workflow/revision/stage; FKs workflow/job/attempt; índice workflow/revision |
| approvals | id/workflow/artifact/decision/operator/scope/key/feedback text; request jsonb+canonical; revision integer; created timestamptz | PK id, UQ workflow/key; FKs workflow/artifact; índice workflow/created |
| workflow_events | sequence identity bigint; workflow/kind text; revision integer; data jsonb; created timestamptz | PK sequence, FK workflow; workflow_event_cursor(workflow,sequence) |
| cognitive_runs | id/job/attempt/provider/key/hash/external_ref/status/remote_status/error text; request/usage jsonb; datas | PK id, UQ key, FKs job/attempt; cognitive_runs_job(job,created); índice status |
| authorizations | id/workflow/job/input_artifact/operator/key/scope/consumed_attempt text; revision/max_runs integer; request jsonb+canonical; data | PK id, UQ workflow/key, FKs workflow/job/artifact/attempt; índice job/revision; max_runs=1 |
| scripts | id/workflow/proposal/hash/status/origin/generator text; revision/version integer; document/review jsonb+canonical quando hash; created timestamptz | PK id, UQ workflow/version; FKs workflow/proposal; preservar origin human/simulated e generator NULL/hermes |
| script_receipts | scope/key text; request/response jsonb+canonical para comparação legada | PK scope/key; índice scope; não inventar FK dentro de response JSON |
| productions | id/script/job/hash/mode text; script_version integer; script_review/plan/artifacts/review jsonb; created timestamptz | PK id, UQ job; FKs script/job; índice script/created/id |
| production_receipts | scope/key/production text; request jsonb+canonical | PK scope/key, FK production; índice production |
| license_reviews | seq identity bigint; id/production/asset/hash/decision/license/evidence/reviewer/supersedes/key text; request jsonb+canonical; reviewed timestamptz | PK seq, UQ id e production/key; FK production/supersedes; license_reviews_asset(production,asset,seq); append-only |
| transmissions | seq identity bigint; id/refs/kind/stage/destination/provider/model/result/detail/hash text; at timestamptz; bytes/tokens bigint; cost numeric | PK seq, UQ id; transmissions_run(run_id,seq); manter refs **sem FK** para probes/recusas anteriores à entidade; append-only |
| human_checkpoints | seq identity bigint; id/action/subject/description/operator/approved_by/currency/ref/key text; approved/expires/consumed timestamptz; limit numeric; request jsonb+canonical | PK seq, UQ id/key; lookup(action,subject,seq); par valor/moeda, CHECKs; no_delete/consume_once |
| radar_cache | key text, fetched/expires timestamptz, payload jsonb | PK key, índice expires; cache até 24h, purge em lote |
| radar_usage | id/bucket text; at timestamptz de epoch | PK id, índice bucket/at; reserva janela 26h |
| radar_blocks | bucket text; until timestamptz | PK bucket; bloqueio 26h; upsert ON CONFLICT |
| radar_datasets | id text, revision integer, expires/collected_at timestamptz, payload jsonb | PK id/revision; índices expires/collected_at/id; latest por DISTINCT ON/janela |

| Tabelas adicionais propostas | Contrato mínimo |
|---|---|
| database_identity | singleton boolean PK CHECK true, instance_id uuid UQ, created_at timestamptz; identidade sem path/inode/DSN |
| schema_migrations | version text PK, checksum text, applied_at timestamptz, app_min/app_max text; app só lê |
| worker_instances | id uuid PK, device_id text, lease_until timestamptz, fencing bigint; jobs ganham claim/lease/token |
| review_comments | id text PK, production FK; hash/timecode numeric/text/author/status/datas; índice production/time/id |
| review_comment_events / review_comment_receipts | Trilha append-only FK comment, seq identity; recibo PK production/scope/key, digest/response jsonb; limites atuais |
| notification_state / preferences | PK device_id/event_id, state read/unread e changed_at / PK device_id/key, value jsonb, updated_at; watermark read_all_before e voz, sem segredos |
| deletion_events | seq identity PK, id UQ, audit_id/ação/alvo/device/data, payload jsonb, prev_hash/hash/canonical; cadeia serializada append-only |
| imports / publishing_receipts | id PK, device/path/hash/manifest jsonb/data / PK production/scope/key, request_hash/response jsonb/status/datas, FK production |

sync_outbox/inbox/cursors não recebem equivalentes PG: transporte híbrido desligado, não domínio. Importador inventaria/reporta linhas inesperadas antes de remover caminho runtime. As **4 tabelas horus_sync** (identities/workspaces/revisions/receipts) não são estas 21; manter migration histórica imutável, desativar caminhos sync e inventariar conteúdo antes de absorver/remover remotamente. Não executar DROP CASCADE nem importar revisão histórica como autorização.

### Segurança e diferenças de semântica

- horus_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS, sem ownership/membership privilegiado. Owner/migrador separado fora do app. CONNECT no banco, USAGE no schema, SELECT/INSERT por tabela; UPDATE/DELETE apenas onde mutável, UPDATE de checkpoint só consumed_at/ref; sequências USAGE, sem setval; sem CREATE/TRUNCATE/TRIGGER/DDL. Auditoria/licenças sem UPDATE/DELETE.
- REVOKE ALL de PUBLIC/anon/authenticated em schema/tabelas/sequências/funções; defaults para **owner real dos objetos**; funções REVOKE EXECUTE FROM PUBLIC explícito, pois default global de EXECUTE não se remove só por schema. SECURITY INVOKER/search_path fixo; nenhum postgres/service_role no runtime.
- **RLS ENABLE+FORCE** em todas as tabelas de domínio, políticas por operação apenas TO horus_app. Estação/proprietário único: USING/WITH CHECK true nas operações permitidas é defesa contra exposição a outros papéis, **não isola usuários do mesmo papel**. Não fingir isolamento com owner_id fornecido pelo cliente. Vários donos exigem papéis segregados/backend remoto confiável, decisão adicional. [RLS PG17](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).
- Triggers PL/pgSQL BEFORE UPDATE/DELETE substituem RAISE(ABORT). consume_once usa IS NOT DISTINCT FROM para imutáveis e OLD.consumed_at NULL→NEW NOT NULL uma vez; consumed_ref obrigatório novo após auditar legado. Expiração/valor conferidos na mesma transação pelo relógio do servidor.
- PG estrito rejeita NULL em PK (SQLite TEXT PK pode aceitar), NUL/Unicode inválido, JSON inválido/NaN, inteiros fora da faixa/datas sem timezone. Relatar rejeitados, sem coerção silenciosa. Bool novo não é inteiro 0/1.
- Collation determinística C nas chaves/ordenação técnica; ordenação UX explícita. PG LIKE é case-sensitive; SQLite ASCII usualmente não: ILIKE só com requisito e testes Unicode/acentos. Não normalizar caminhos/IDs legados.
- ?→%s Psycopg; sqlite3.Row→dict_row/DTO; json_extract→->/->> com casts, diferenciando SQL NULL/JSON null/campo ausente; last_insert_rowid→RETURNING, rowid removido; CAST BLOB→octet_length/bytea.
- NULLS FIRST/LAST e desempates por id explícitos; datas por instante UTC, não texto ISO; sequence tem lacunas e não garante ordem de commit.
- Identity GENERATED ALWAYS recebe sequência legada via **OVERRIDING SYSTEM VALUE**, depois setval acima de MAX pelo migrador, tratando vazio; IDs text/FKs intactos. [INSERT PG](https://www.postgresql.org/docs/17/sql-insert.html).

## 5. Conexão, disponibilidade e latência

**Recomendação: Psycopg 3 com psycopg_pool.ConnectionPool síncrono**, inicialmente 1–4 conexões + uma dedicada de coordenação. Código SQL atual é síncrono e já usa threads; asyncpg exigiria refatoração async ampla sem ganho demonstrado. Rotas async/workers chamam blocos SQL via asyncio.to_thread/threadpool, sem bloquear event loop. Uma conexão por UoW, nunca compartilhada simultaneamente. [Pool Psycopg](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

| Item | Contrato proposto |
|---|---|
| Pooler | **Supavisor sessão 5432**, backend desktop persistente/IPv4; advisory de sessão só em conexão dedicada. 6543 transacional economiza conexões, mas não oferece estado estável de sessão; só adotar após ensaio real com tenant e sem session locks |
| Prepared statements | Sessão suporta; iniciar prepare_threshold=None até paridade; em 6543 desabilitar, sem presumir flag do serviço. Upstream suporta named statements sob flag, mas projeto hospedado pode não habilitá-la |
| TLS | sslmode=verify-full + sslrootcert CA Supabase obtida de fonte oficial/projeto, embarcada como certificado **público**, fingerprint/revisão e plano de rotação. Hostname DNS do pooler verificado; nunca afrouxar para require/CA corporativa ao falhar |
| Configuração/cofre | Usuário entrega host/porta/database/user/password uma vez via POST loopback autenticado, origem permitida/tamanho limitado, sem logs de corpo/access sensível; backend valida destino/opções, conecta e grava keyring por perfil. Senha write-only: GET só estado/rótulo sanitizado, nunca DSN/user/senha. Sem senha em env/CLI/Git/instalador/frontend persistente |
| Limite do segredo | Backend lê senha em memória; keyring não protege contra mesmo usuário comprometido. Credencial exclusiva do proprietário, nunca segredo compartilhado no pacote. Cofre indisponível falha fechado; testes MemoryKeyring autouse |
| Limites iniciais, medir | connect_timeout 5s, pool acquire 3s, statement_timeout 15s, lock_timeout 2s, idle_in_transaction_session_timeout 15s; migração/dump fora das rotas com orçamento próprio; pool/fila limitados |
| Retry | Backoff+jitter limitado, p.ex. 1/2/4s até 3; só leitura segura ou transação inteira sem efeito externo. 40001/40P01 repete UoW inteira. Resposta COMMIT perdida→consultar recibo mesma chave; nunca retry cego de gasto/publicação/Hermes |
| Egress | Pooler TCP5432 entra no inventário autorizado: gateway HTTP atual não cobre libpq/TCP. Validar endpoint; sem aceitar DSN arbitrário/opções de serviço/destino local via formulário, sem desativar barreiras |
| Inicialização | API sobe diagnóstico sem BD, sem inicializadores que migram/purgam. Operações devolvem 503/código estável; liveness distinta de readiness do BD; não criar arquivo SQLite |

| Estado | Comportamento |
|---|---|
| not_configured | Configurar; operações bloqueadas com instrução clara |
| connecting | Tentativa limitada, sem anunciar pronto/iniciar jobs |
| connected | TLS, papel/grants e schema compatíveis confirmados |
| unreachable | Internet/DNS/timeout/pausa; 503 e reconexão limitada/explicita |
| auth_failed | 28P01/28000, sem loop infinito; corrigir pelo fluxo write-only |
| schema_mismatch | Versão/checksum/objeto obrigatório incompatível; bloqueia operação e orienta manutenção |
| migrating | Manutenção remota, drain de workers e recusa de novas operações; app não inicia migração |

TLS inválido tem código database_tls_invalid sob unreachable; papel incorreto database_role_invalid, não confundir com auth_failed; nenhuma resposta inclui SQL/DSN. Congelar contrato HTTP de status antes do frontend.

| Consultas tagarelas verificadas | Adaptação antes de medir rede |
|---|---|
| Store.event | Até insert+last_insert_rowid+select+update+delete por segmento; RETURNING/update atômico, lote limitado/poda periódica, mantendo texto+cursor atômicos e checkpoint reconhecido durável |
| Store.recover/JobManager.start | get/update/event por job; UPDATE RETURNING e eventos em lote; recuperar só lease expirada, nunca jobs vivos de outro PC |
| WorkflowService.get | ~7 consultas sequenciais; pipeline/agregados limitados e snapshot coerente se revisão muda |
| _create_steps/_cancel_remaining/retention por workflow | executemany/pipeline/CTEs/set-based, transações curtas, índices; sem conexão nova por item |
| ProductionService.list→record | N+1 de consulta de job por produção; JOIN/carregamento em lote, sem I/O repetido de arquivo dentro da transação |
| RadarState.cached/load/list_datasets | Purge com 4 DELETEs por leitura; purga periódica/em lote e SELECT com filtro expires, índice/latest revision |
| Radar.invalidate e orçamento security | Varredura JSON em Python e histórico repetido; filtro JSONB/batches e agregado único sob lock de orçamento |
| Notificações/error_center/deletion preview | Counts repetidos e paginação/filtro Python; agregar no servidor. Benchmark com RTT sintético 50/150ms |

Internet cai durante produção: bloquear novos jobs/etapas/efeitos externos; trabalho puramente local já iniciado pode concluir **somente arquivo em staging**, sem marcar sucesso/publicar nem criar banco/fila JSON local. Ao reconectar, reconciliar arquivos por hash e recibos PG; ambiguidade externa→unknown+revisão humana, sem replay automático. Sem BD, não aceitar aprovação/consumir checkpoint. Reinício offline mostra erro e preserva arquivos.

## 6. Transações, concorrência e versão

| SQLite atual | PG proposto / aceite |
|---|---|
| BEGIN IMMEDIATE global | READ COMMITTED + FOR UPDATE da raiz workflow/job/production; ordem workflow→job→attempt→entidade, sem lock global |
| Chave idempotente ainda ausente | UQ+INSERT ON CONFLICT DO NOTHING RETURNING/releitura canônica, ou advisory xact por escopo/chave; SELECT FOR UPDATE **não trava ausência de linha** |
| Aprovação/versão/cancelamento | Lock raiz+reread revisão/hash/estado+CAS; estado/recibo/evento numa transação; duas sessões não criam duas versões |
| Consumo único | UPDATE WHERE consumed_at IS NULL AND expires_at>clock_timestamp() RETURNING + trigger; um vencedor; consumo e intenção durável na mesma UoW, sem conexão oculta |
| Orçamento/Radar.reserve | pg_advisory_xact_lock por orçamento/bucket ou linha dedicada; agregado+reserva atômicos. SERIALIZABLE apenas para invariantes agregadas que precisarem, com retry integral limitado |
| Radar.collection_lock/WorkerLock | Advisory de sessão dedicado + lease/fencing/timeout cooperativo, sem transação durante rede/render. Perda da conexão invalida executor e impede próximo efeito |
| Eventos/SSE por sequence | Lock raiz **antes de obter identity** em todos writers de um job/workflow; feed global transmissions/checkpoints precisa lock comum de append antes de INSERT ou cursor por commit: sequence sozinha pode pular commit tardio |
| Crash/retomada | Claim/lease/token; commit final exige token vigente; não recuperar jobs alheios vivos. Arquivo+PG sem transação distribuída: staging/hash/reconciliação |

Substituir user_version por migrations timestampadas e horus.schema_migrations(version,checksum,...), confrontando ledger/contrato/objetos e supabase_migrations, sem duas listas concorrentes de DDL. **Startup/instalador só verificam**. Migrador de manutenção separado usa papel temporário fora do app, backup verificado, advisory lock de migração, marcador migrating, drain e aplicação transacional/checksum/validação. Falha bloqueia com diagnóstico; compatibilidade de versões explícita. App atualizado pode aguardar manutenção, jamais ganhar DDL. CREATE INDEX CONCURRENTLY não roda em transaction block.

## 7. SQL versionado e migração verificável

Manter supabase/migrations/20261115150000_sync_metadata.sql **imutável**; novas migrations roles/schema→21 tabelas/FKs/índices→triggers/grants/RLS→tabelas JSON/coordenação. SQL operacional portável PG17, sem auth.users/extensões. Cadeia antiga exige harness com roles/auth sintéticos **só no teste** ou suíte separada operacional; documentar diferença, não editar histórico para plain PG passar.

| Ferramenta futura backend/tools/migrate_sqlite_to_pg.py | Critério / recuperação |
|---|---|
| 0 prévia | Origem cópia explícita, destino descartável allowlisted/não vazio recusado, dry-run padrão; recusar pastas operacionais/instalação. Sem segredo em argv/log; nenhuma migração de dados realizada agora |
| 1 preservar | Exportação completa permitida+inventário de mídia separado, hash e snapshot via SQLite backup API **da cópia**, incluindo WAL; integrity_check/foreign_key_check/user_version; backup imutável |
| 2 mapear | Tabelas explícitas por versão, Radar separado, opcionais sync e JSON; inesperados rejeitados/relatados. Mapear v9–v12 diretamente ou migrar outra cópia com legado fixado, nunca origem |
| 3 carregar | Uma transação: workflows current_artifact NULL→jobs em ordem depends_on→attempts→artifacts→atualizar current_artifact→approvals/runs/authorizations→scripts/receipts→productions/receipts→reviews→events/workflow_events/transmissions/checkpoints; Radar independente e JSON após pais |
| 4 preservar | FKs cíclicas DEFERRABLE/SET CONSTRAINTS ALL IMMEDIATE antes do commit; supersedes topológico; IDs/seq/chaves/hashes/canonical preservados; OVERRIDING SYSTEM VALUE/reseed; carga nunca executa autorização |
| 5 conferir | Contagens por tabela/NULLs/min/max IDs e seq; SHA256 de linhas em ordem PK e hash por tabela usando **o mesmo mapeamento canônico**, conversões comparadas semanticamente; conteúdo original por bytes originais |
| 6 integridade | Zero órfãos/UQ violada, constraints validadas, JSON/data/Decimal válidos; relatório migrados/rejeitados/inconsistências sem conteúdo privado; rejeitado→rollback e exit !=0, nunca sucesso parcial |
| 7 falha/repetição | run_id/checksum da origem; rollback total da carga, destino descartável novo para repetir, origem/backup intocados. COMMIT ambíguo→consultar run_id antes de repetir; sequences podem avançar mesmo em rollback |
| 8 ensaio futuro | Dump/export PG→restore isolado→contagens/hashes→testes/crash; dados reais só com autorização separada, destino/contas/janela de corte definidos |

DDL pode preceder carga; falha não grava “migrado” no ledger. No corte real, congelar gravação da origem para não perder mudanças depois do snapshot. Tag legacy/sqlite-persistence recupera **código**, não desfaz dados PG criados depois: sem retorno automático ao SQLite.

## 8. Testes locais e CI: decisão crítica

| Opção | Evidência / recomendação |
|---|---|
| **pgserver 0.1.4 PyPI cp312 win_amd64** | **Baixado, instalado e executado sem admin**; wheel ~12,8MB, PG **16.2**, sem Docker. Python 3.12.14: initdb **3,075s**, start **0,333s**, stop **0,169s**; SELECT version(), JSONB e identity aprovados; bind 127.0.0.1; postmaster.pid ausente após stop. M1-pg-local-result.json; Psycopg 3.3.9/psycopg_pool 3.3.3. SHA256 da wheel informado no PyPI: 406e9355334e40754160a33d93f18a848720a38cd0b68da50be2ea272c89ed2d |
| Limites da prova | Não valida PG17/Supavisor/TLS remoto/RLS proposto/suíte Hórus. PG16.2 antigo só serve à prova sintética descartável, não empacotar no produto nem usar como paridade definitiva |
| Harness Windows | Primeira tentativa com stdout=PIPE reteve handles herdados do pg_ctl; servidor explicitamente parado. Corrigido para stdout/stderr em arquivo, CREATE_NO_WINDOW e finally stop; usar essa forma no I0, sem serviço Windows |
| **Recomendação definitiva: ZIP EDB PG17** | [PostgreSQL oficial](https://www.postgresql.org/download/windows/) aponta [ZIP EDB](https://www.enterprisedb.com/download-postgresql-binaries); extrair sem instalador/admin, major17 como supabase/config. Fixar release atual, checksum/proveniência/licença em ferramentas; dados TEMP. **Esse ZIP não foi executado nesta sessão**: a consulta HTTPS local à página EDB retornou HTTP 500 handshakefailed (sem afrouxar TLS); leitura web oficial disponível. Gate I0 antes de converter fixtures |
| postgresql-wheel | [PyPI](https://pypi.org/project/postgresql-wheel/) descreve suporte efetivo Linux; classificador Windows não basta. Não recomendado para esta máquina |
| testing.postgresql | [PyPI](https://pypi.org/project/testing.postgresql/) exige binários no PATH, não os fornece. Harness mínimo sobre ZIP oficial evita dependência que não resolve o problema |
| CI | PG17 service container em ubuntu-latest ou ZIP EDB windows-latest; Docker no runner não exige Docker neste PC. Provisionador separado de horus_app; dados sintéticos; cache binários com hash/venv-lock, não pgdata operacional |

I0: initdb em TEMP exclusivo/prefixado, porta efêmera, listen_addresses=127.0.0.1, sem firewall/serviço; verificar caminho antes de remover. Trust **apenas** na prova local sintética loopback; suíte definitiva cria login mínimo sintético e negativos senha/grants. TLS negativo com certificados locais, sem trust global. Venv da prova nesta pasta; dados TEMP parados preservados para inspeção, sem exclusão em massa.
Recomendação em duas etapas: usar pgserver somente para desbloquear protótipos sintéticos agora; I0 deve validar ZIP PG17 e substituir o binário antigo antes do aceite da suíte definitiva. Não adicionar pgserver ao runtime do produto.
Fixtures: cluster por sessão/worker pytest; migrations uma vez em template; banco clone por teste com commits/múltiplas conexões/API/subprocesso. Schema isolado só com estrutura/config equivalentes; rollback/savepoint apenas se unidade presa à **mesma conexão**, nunca fingir isolar API/threads. Fechar pool antes de DROP DATABASE; suíte normal recusa DSN não local. Não initdb por caso (~3s × 2,9mil); benchmark clones/DDL/tempo total no I0 decide granularidade. App/testes não rodam como owner/superuser.
Suíte dev Supabase opt-in, após usuário configurar acesso: TLS positivo/CA+hostname negativos, login horus_app sem DDL/BYPASSRLS, RLS FORCE/grants/defaults, checkpoint/orçamento concorrente, replay/COMMIT ambíguo, reconexão5432/6543 se adotada, ledger/catalog/hash paritários PG17, dump/restore sintéticos e logs sem segredo. Reusar padrões supabase/tests/isolation.sql e audit.sql, sem presumir claims Auth horus_sync. Nenhum teste remoto executado.

## 9. Impactos e incrementos

| Recurso | Mudança necessária |
|---|---|
| backups.py/local_data_export | Formato novo versionado: dump lógico PG + manifest/hash e arquivos locais permitidos separados; pg_dump/restore major compatível. Credencial backup restrita fora de argv/log/Git; acesso via cofre e mecanismo libpq protegido/temporário revisado, sem segredo no pacote |
| Restauração | Sempre instância/banco isolado, nunca sobrepor remoto pela UI. Restore exige owner/migrador externo; export pelo app pode usar SELECT/COPY com RLS; dump completo via manutenção separada, sem postgres/service_role no app |
| HOR-157 v9→v12 | Obsoleto para upgrade PG; preservar como compatibilidade/evidência do importador legado; não executar na instalação real |
| HOR-100/163 atualizador | Preservar contrato /v1/update/backup/rótulo seguro; atualização do binário não migra banco. Backup local de arquivos+comprovante remoto recente/export lógico permitido; indisponível/incompleto bloqueia update; combinar com owner, sem editar arquivos dele |
| HOR-117 | Manter prévia/frase/token/backup/quarentena local; intenção auditada PG antes de mover e resultado depois. Rede cai após movimento→incompleto reconciliável por hash, sem prometer transação disco+PG |
| Retenção/privacidade | PG MVCC/WAL/backups não garantem zeragem física imediata; retirar promessa secure_delete e documentar retenção provedor; minimizar conteúdo em auditoria |
| Cofre/auditoria/gates | Keyring isolado em testes, sem segredo em DB/export. Intenção/checkpoint não persistiu→bloquear transmissão; best_effort só recusa/desfecho sem efeito novo autorizado |
| Radar/jobs/pipeline | Migrar cache/cota/trava/CLI também; claim/lease/fencing por dispositivo; medir latência e checkpoint antes do corte |

Donos abaixo são **proposta**, nenhuma delegação executada. Cada incremento possui testes exclusivos backend/tests/pg/test_<dominio>*.py; arquivos compartilhados têm integrador único. Módulos ainda SQLite durante desenvolvimento não podem compor versão publicada de corte PG.

| Incremento / dono | Arquivos/pastas exclusivos | Dependências | Aceite / testes / risco |
|---|---|---|---|
| I0 PG17 / Codex | backend/tests/pg/harness*, conftest.py local, scripts/test-postgres*; CI pelo owner .github | Revisão M1 | PG17 sem admin start/stop, papel mínimo, isolamento/benchmark; risco DLL/download/runner |
| I1 acesso/config/status / Codex | database/ (novo), testes pg/database; dependências entregues ao integrador | I0 | Pool/DTO/UoW, keyring write-only, 7 estados/TLS/timeout, senha/CA errada sem SQLite; risco event loop |
| I2 SQL / Codex | novas supabase/migrations/*horus*.sql, tests/horus_* | I0+contratos I1 | Schema/FKs/triggers/grants/RLS/ledger, fresh/upgrade/rollback/2 sessões; risco hash/privilégios |
| I3 jobs/mídia / Codex | storage.py, worker_lock.py, jobs.py, testes pg/jobs | I1+I2 | Eventos/checkpoint/claim/recover/media PG; duas instâncias não recuperam job vivo, replay sem duplicação; risco split brain |
| I4 workflow/gates / Codex | workflows.py, workflow_hermes.py, human_checkpoints.py, transmissions.py, security.py, testes pg/workflows | I1+I2; contrato I3 | Atomicidade autorização/orçamento/checkpoint, unknown/replay concorrente sem inferência real; risco gasto duplicado |
| I5 produção / Codex | scripts.py, production.py, license_reviews.py, testes pg/production | I1+I2+contratos I3/I4 | Versão/revisão/hash/recibos e append-only, produção simulada/crash; risco stale approval |
| I6 Radar / Codex | radar_youtube/state.py, __main__.py local, retention.py, testes pg/radar | I1+I2, wiring I3 posterior | CLI/cache/cota/lock sem SQLite e retenção sem promessa física; risco corrida de cota |
| I7 JSON/leitura / Codex | notifications.py, review_comments.py, error_center.py, publishing.py, exports.py, narration/service.py, project_import.py, testes pg/editorial | I1+I2+contratos I5 | Estado operacional PG, arquivos apenas artefatos, recibos/comentários/limite N+1; risco disco/BD |
| I8 backup/exclusão / Codex | backups.py, local_data_export.py, update_backup.py, data_deletion.py, testes pg/backup | I1+I2+contratos I7 | Formato/restore isolado/hashes/quarentena reconciliável/gate update; risco backup incompleto |
| I-mig / Codex | backend/tools/migrate_sqlite_to_pg.py, testes pg/test_migration_*, fixtures legadas sintéticas | I2 e mapeamentos I3–I8 | v9/v12/Radar/JSON, IDs/FKs/contagem/hash/rejeitados/rollback; nunca dado real |
| I-UI / Antigravity | frontend/src componentes/serviço conexão/diagnóstico e testes próprios, sem Ajustes→Atualizações HOR-163 | Contrato I1 congelado | Estados claros, senha write-only, bloqueio offline/recuperação sem banco alternativo; risco UX |
| I-final / Codex integrador+Maestro | api.py, config.py, __main__.py raiz, pyproject/lock, persistence_contracts.py, conftest global, scripts legados/medidas e docs; Tauri/.github pelos owners | Todos + teste dev autorizado | Wiring exclusivo PG/pacote sem SQLite; suítes completas e transversais, frontend/E2E sintético/migração/recovery; risco corte incompleto |

Ordem: I0→I1/I2 (contratos juntos)→I3/I4/I6 e I-UI independentes→I5→I7/I8 e I-mig→I-final. Serializar I3/I4 se contrato claim/gates mudar. Revisões separadas, **sem publicar híbrido**; um dono por arquivo. Importador e fixtures de migração são únicos consumidores SQLite temporariamente permitidos.
**Gate nenhum SQLite escondido:** scan AST import/importlib e bloqueio runtime de sqlite3/_sqlite3 na jornada PG completa; allowlist exata somente processo separado do importador/fixtures legadas. Buscar sqlite/PRAGMA/last_insert_rowid/rowid/INSERT OR/sidecars em backend/scripts/Tauri; teste startup+queda de rede não cria .db/.sqlite*/refresh-lock; scan pacote/dependências e libsqlite inesperada. Remover SQLite do runtime e testes operacionais, não apenas import principal; tag/ferramenta explícita não são fallback.

## 10. Riscos e decisões pendentes

| Risco/decisão | Recomendação / responsável |
|---|---|
| Sem internet = sem operação nova | Consequência da decisão exclusiva PG; explicitar UX/docs. Ferramentas independentes locais continuam independentes, sem prometer app offline |
| Conteúdo privado em terceiro | Minimizar conteúdo/mídia local; região/termos/retenção documentados. Dados reais exigem autorização específica posterior |
| Local First e senha no backend desktop | Registrar exceção arquitetural e limite estação/proprietário confiável; cofre protege repouso, não processo comprometido. Não reaplicar ADR antigo para bloquear plano autorizado, nem prometer segurança multiusuário |
| Plano Free/pausa/limites/backups | Sem SLA/PITR/backup gratuito presumidos; [backups oficiais](https://supabase.com/docs/guides/platform/backups) recomendam export regular no Free. Antigravity E2 confirma limites/pausa atuais sem upgrade/cobrança; usuário define RPO/RTO/destino de backup |
| Acesso Supabase | Usuário realiza login/provisionamento no terminal quando liberado e configura horus_app/CA pelo fluxo seguro; nunca pedir valores em chat. Nada remoto alterado agora |
| JSON e vários PCs | Aprovar tabelas novas de JSON/identidade por dispositivo; primeiro corte estação única mantém UQ path/execução exclusiva. Compartilhamento multiusuário exige decisão adicional |
| Liberação | Maestro revisa plano e libera incrementos; migração de dados reais/corte da instalação/publicação têm gates separados |

Fontes adicionais: [conexões Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres), [SSL/CA](https://supabase.com/docs/guides/platform/ssl-enforcement), [FAQ Supavisor](https://supabase.github.io/supavisor/faq/), [instalação Psycopg](https://www.psycopg.org/psycopg3/docs/basic/install.html), [pgserver PyPI](https://pypi.org/project/pgserver/). Recomendações são propostas; só a prova sintética PG16.2 foi executada.

Validação da entrega: 281 linhas antes desta nota, referências dos artefatos e módulos conferidas, Git sem diff/alterações ao final; prova PG local aprovada e instâncias dos dois ensaios encerradas. Suítes runtime Hórus, conexão Supabase, ZIP PG17 e migração de dados não executados; não houve commit/push/PR.
