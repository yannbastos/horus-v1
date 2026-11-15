# Anexo — inventário e contrato proposto de persistência

**HOR-137 / Fase A, Codex, 15/11/2026.** Complementa o [ADR](adr-persistencia-local-remota.md). Diagnóstico de código, não inventário de registros reais. Base `399f305341b9fc89f21edae4198ed1ed48fc5b6a` (`origin/main`); linha retida `d365cc584d95fdddfe8a36ce9ca4aed6e66ccfe4` (`origin/integ-hermes-line-v3`) apenas lida com `git show`. Nenhuma integração dessa linha, nenhum banco operacional aberto, nenhuma migração remota ou local aplicada.

## 1. Ambiente e limites da evidência

- Branch Orca efetiva: `yannbastos/docs-hor-137-persistence`, criada sobre `5901cba` e avançada por fast-forward para `399f305` antes da entrega. Prefixo do Orca preservado.
- `supabase` não está no PATH (`Get-Command`/`where.exe`); não havia shim encontrado em Scoop, WinGet Links ou npm global. Executável existente encontrado no cache npm: `%LOCALAPPDATA%/npm-cache/_npx/aa8e5c70f9d8d161/node_modules/@supabase/cli-windows-x64/bin/supabase.exe`.
- Esse executável respondeu `3.1.0` a `--version`; `projects list` retornou `horus-dev`, `ACTIVE_HEALTHY`, PostgreSQL `17.11.0.019`, região `us-east-2`, `linked:false`. A autenticação de gerenciamento está funcional nesta sessão. Não se leu nem exibiu o mecanismo/token de autenticação. Nenhum login foi feito.
- Complemento após mensagem do Maestro: o login foi realizado pelo próprio usuário; o agente não executou login. Repetidos os comandos autorizados `npx supabase --version` e `npx supabase projects list`, ambos com saída zero: versão `3.1.0`, mesmo projeto/ref/região/Postgres e `linked:false`, sem alteração remota.
- Project Reference confirmado: `uzirimqmwohnfraszvjn` (identificador, não credencial). A listagem informa organização por ID, não confirma o nome exibido Yann Labs. Plano Free, Data API desligada, exposição automática desligada e RLS automático ligado são premissas fornecidas pelo usuário, não configurações auditadas por `projects list`.
- O CLI também emitiu `Cannot find project ref. Have you run supabase link?`; isso não impediu a listagem autenticada. **Não executar `link`** nesta fase. Não foram executados `db push`, dump, deploy ou comandos de alteração no projeto.
- Se em outra sessão faltar autenticação, cabe ao usuário executar interativamente `npx supabase login` e depois `npx supabase projects list`; não passar token por chat, argumento ou arquivo versionado. Caminho do cache é circunstancial, não configuração do produto.
- `docs/seguranca-persistencia-remota.md` foi lido na base. `docs/ux-sincronizacao.md` ainda não estava na `main` consultada: a cópia em trabalho do worktree `docs-hor-137-ux` foi lida sem alteração; o Maestro deve garantir sua integração antes de aceitar os links documentais finais.
- `ARCHITECTURE.md` ainda menciona schemas v5/v7; o código canônico `migrations.py` define **v9**. Não tratar texto antigo como schema real nem promover v12 à `main` por inferência.

## 2. Bancos e consumidores

| Armazenamento | Definição e consumidores | Observação |
|---|---|---|
| `<data_dir>/horus.sqlite3` | `api.py:91`, `storage.py`, `migrations.py`; JobManager/transcrição/download; `workflows.py`, `workflow_hermes.py`, `scripts.py`, `production.py`, `license_reviews.py`, `publishing.py`, `exports.py`, `narration/service.py`, `notifications.py`, `error_center.py`, `security.py`, `retention.py` | Estado oficial local, JSON em colunas TEXT, UTC textual, UUID hex; `Store` migra ao inicializar: não iniciar API contra banco real para diagnosticar |
| `<data_dir>/radar/radar-youtube.sqlite3` | `radar_youtube/state.py`, coletor e API Radar | Banco independente, sem `user_version`/cadeia v1–v9; inicialização cria tabelas e purga expirados, portanto não instanciar sobre dados reais nesta fase |
| `radar-youtube.refresh-lock.sqlite3` | `RadarState.collection_lock()` | Banco auxiliar sem tabelas, `BEGIN EXCLUSIVE`, espera cancelável limitada; não transportar como dado |
| `horus.backup-v<N>-<uuid>.sqlite3` | `migrations.py`, `backups.py` | Snapshot anterior à migração via `sqlite3.backup`; inventário/validação/redação de cópias não equivale a backup remoto |
| SQLite de pacotes locais e restaurações em pasta nova | `local_data_export.py` | Inclui snapshots operacional/Radar e JSON permitidos; não copia credenciais/mídia/modelos. Imports de projeto são outro mecanismo |
| Bancos externos Hermes/fábrica | Fronteiras independentes; `security.py` reconhece `cron/executions.db` no perfil Hermes | Não pertencem ao schema Hórus nem à transferência; nenhuma varredura de seus registros/configurações privadas realizada |

`data_dir` varia por modo desktop/backend e `HORUS_DATA_DIR`; não presumir que instalações compartilham a mesma base. WAL/SHM são sidecars de operação, não arquivos para sincronizar entre PCs. Não há outro motor de banco operacional Hórus identificado na busca do código de produção desta base. Isso não prova ausência de arquivos históricos na máquina.

### Migrações canônicas da main

| Versão | Efeito |
|---|---|
| 1 | `jobs`, `events`, `media`; índice de eventos por job e sequência |
| 2 | Workflows, tentativas, artefatos, aprovações, diário; jobs ganham workflow/revisão/etapa/dependência; unicidade de etapa |
| 3 | Executor do workflow; `cognitive_runs` e `authorizations`, recibos/hashes e consumo por tentativa |
| 4 | `workflows.cognitive_client` (`live`/`simulated`), sem execução remota implícita |
| 5 | `scripts`, versões/revisões humanas e `script_receipts` |
| 6 | `productions` e `production_receipts` |
| 7 | Reconstrói produções/recibos em transação para ampliar CHECK de modo a `local_pilot`; preserva IDs e FKs |
| 8 | `license_reviews`, índice por produção/asset/seq, triggers que proíbem UPDATE/DELETE |
| 9 | `scripts.origin` (`simulated`/`human`), default para versões anteriores |

`migrate()` valida versões futuras, recusa banco sem versão com tabelas, habilita FK/WAL, abre `BEGIN IMMEDIATE`, salva backup quando há versão anterior, executa statements individualmente e atualiza `PRAGMA user_version` na mesma transação. Não usar `executescript` como substituto: pode finalizar transação implicitamente. Num porte PostgreSQL, manter histórico remoto próprio; não reaproveitar os números locais nem executar ALTER/DROP de SQLite no remoto.

### Linha retida v10–v12 — somente diagnóstico

| Versão | Efeito e restrições |
|---|---|
| 10 | `scripts.generator` anulável, CHECK `NULL` ou `hermes`; não muda o CHECK `origin` da v9 |
| 11 | `transmissions`: auditoria de metadados, `seq` autoincremento, ID único, kinds/results enumerados, bytes/tokens não negativos, hash 64 caracteres, detail até 120; índice `transmissions_run(run_id,seq)`; triggers no-update/no-delete. Referências de workflow/job/run são TEXT, **sem FKs declaradas** |
| 12 | `human_checkpoints`: ação enumerada, subject hash com 64 caracteres, descrição 1–200, aprovador humano, operador/expiração, par valor/moeda, idempotência única; índice `human_checkpoints_lookup(action,subject,seq)`; no-delete e UPDATE limitado a consumo único, mantendo identidade/conteúdo |

Esses conceitos ficam fora da transferência inicial. Se integrados futuramente por decisão independente, auditoria pode ganhar réplica histórica permitida; checkpoints de gasto/publicação jamais serão consumidos automaticamente pelo recebimento remoto. O contrato de valor monetário TEXT deve ser validado antes de eventual tipo decimal remoto, sem arredondamento implícito.

### Radar

Quatro tabelas, sem índices explícitos adicionais: `radar_cache(key PK, fetched TEXT NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL)`; `radar_usage(id PK, at REAL NOT NULL, bucket TEXT NOT NULL)`; `radar_blocks(bucket PK, until REAL NOT NULL)`; `radar_datasets(id NOT NULL, revision INTEGER NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL, PK(id,revision))`. Não há FKs/CHECKs no DDL Radar. Expiração, validação de payload e limites estão no Python, não presumir proteção SQL equivalente. Cache até 24 h; reservas/bloqueios em janela conservadora de 26 h; datasets têm expiração própria; retenção de cópias Radar em workflows usa regra de redação de 28 dias.

## 3. Estado fora do banco

| Arquivo/área | Semântica e risco de transferência |
|---|---|
| `notifications-state.json` | `read`, `unread`, `read_all_before`; eventos derivados por consulta do banco, não duplicados no JSON. Retém leitura 90 dias, máximo 5.000 IDs; JSON corrompido avisa e pode ser recriado na escrita seguinte; escrita temporária + `os.replace` e lock de processo |
| `production-comments/<production_id>.json` | `schema_version=1`, comentários por hash do vídeo/timecode, trilha append-only e recibos idempotentes; até 500 comentários, 50 eventos por trilha, 1.000 recibos. Autor atual é rótulo humano, não identidade Auth verificada. Ilegível resulta em 409, sem sobrescrever. Lock por produção no processo e replace atômico não fornecem transação com SQLite nem exclusão entre computadores |
| `imports/<id>/import.json` + arquivos | Registro novo em staging e promoção atômica por rename; hashes/tamanhos dos arquivos, proveniência e histórico. `historical_only=true`, `approved=false`; revisão/licenças/QC do pacote não são aprovação neste computador. Não converter import em workflow ou permissão por sync |
| `narration-preference.json` | Preferência local não secreta do provedor/voz; metadados não transferem modelos, licença ou capacidade da outra máquina |
| `productions/<id>/*.json` e subpastas | Planos, manifestos, `pilot-evidence.json`, QC, alinhamento, proveniência e metadados de publicação; há também `publishing/.../receipt.json` e marcador `failed.json`. Coordenar registro SQLite e integridade por hash, sem presumir atomicidade disco+banco |
| Pacotes/exports | `manifest.json`, roteiro, QC, licenças, revisão e mídia do pacote. Backup de dados locais tem allowlist própria: não confundir com export audiovisual nem presumir que todo subdiretório/import é coberto |
| Configuração Hermes, perfil de segurança, credenciais de provedores/cofre | Fora da sincronização e de pacotes; não abrir, copiar ou registrar valores nesta análise |
| `localStorage` WebView | Tema, atalhos, recolhimento do guia, prontidão, preferências de lançamento e recibo de recuperação de transcrição/texto anterior referenciado (`transcriptionRecovery.ts`). Recibo inclui identidade local da base; não é fila distribuída nem identidade portátil de backup; não sincronizar token, recibo ou caminho |

Consequência: não basta exportar tabelas. Comentários pendentes precisam de migração futura específica para outbox transacional, ou journal durável e reconciliação, antes de oferecer sync. Nesta fase eles continuam JSON, sem refatoração. Corrupção tolerada em notificações não deve ser copiada para comentários/decisões como política geral.

## 4. SQL específico e impacto no porte

| Padrão e evidência | Tratamento futuro |
|---|---|
| `PRAGMA user_version`, FK, WAL (`migrations.py`); `secure_delete`, checkpoint TRUNCATE, freelist/page_count (`storage.py`); integrity_check (`backups.py`) | Continuam somente no adaptador SQLite; PostgreSQL tem catálogo, migrações e operação/backup próprios; WAL remoto não é sidecar local |
| `sqlite_master`, `sqlite3.Row`, exceções `sqlite3.*`, placeholders `?` | Mapear no adaptador; contratos não retornam conexão, cursor, Row ou exceção de driver; catálogo PostgreSQL separado |
| `AUTOINCREMENT` em eventos, diário, licenças (v11/v12 retidas também) | Identidade/sequence remota para paginação, UUID como identidade de evento; sequências podem ter lacunas e não são relógio global nem ID portátil |
| `json_extract` em `Store.list_jobs`, `error_center.py`, `retention.py`, `backups.py` e Radar | JSONB/operadores no remoto somente para campos permitidos; extrair campos de filtro/owner/revisão para colunas; comparar diferença JSON null/SQL NULL/texto |
| `julianday(json_extract(...))` em `RadarState.list_datasets` | Coluna timestamptz explícita e índice quando necessário; não depender de parse de data em cada leitura |
| `datetime(...)` SQL | Não localizado nos arquivos Python de produção desta base; datas são majoritariamente geradas/interpretadas por Python. Não confundir chamadas Python `datetime` com função SQL; remoto usa UTC/timestamptz e relógio de servidor para sync |
| `INSERT OR REPLACE` de cache/bloqueios Radar | PostgreSQL `ON CONFLICT DO UPDATE`; REPLACE SQLite pode apagar/reinserir, não copiar sem revisar efeitos/FKs |
| Upsert `media` por `path` em `storage.py` | Preservar comportamento local; remoto não identifica mídia por caminho global, usa hash/manifesto permitido |
| `BEGIN IMMEDIATE` em Store, migrações, workflows, scripts, produção, licenças, publishing, retenção, Hermes e Radar | Não traduzir mecanicamente: transação Postgres com unicidade, versão esperada e lock por agregado/linha; testar corrida e rollback. Não segurar lock durante rede ou render |
| `BEGIN EXCLUSIVE` no lock Radar e trava de worker por arquivo (`worker_lock.py`) | São coordenação local, não eleição distribuída; manter fora de sync |
| Triggers SQLite `RAISE(ABORT,...)` e comparações `IS` (v12) | Recriar/revisar regras em PostgreSQL e privilégios; comparação nula exige `IS NOT DISTINCT FROM` quando equivalente; testar consumo único/append-only com SQL direto |

Não há camada de repositórios completa hoje: módulos acessam `store.connect()` e SQL diretamente, e a API captura `sqlite3.IntegrityError`. A extração futura deve ocorrer por domínio e preservar transações existentes, sem uma troca global de driver ou ORM novo por conveniência.

## 5. Contratos estáveis propostos — sem implementação

Preservar rotas/payloads públicos e contratos da fábrica/Hermes. Nomes abaixo são portas conceituais internas a refinar na Fase B; nenhum `Protocol` foi adicionado ao código.

| Porta | Operações mínimas / garantias |
|---|---|
| `LocalUnitOfWork` | `begin()`, `commit()`, `rollback()`; repositórios do mesmo arquivo e outbox compartilham transação; nada de HTTP/render dentro dela |
| `JobRepository` | `submit(payload,key)`, `get(id)`, `list(filter,cursor)`, `checkpoint(id,attempt,event,result)`, `transition(expected,status)`; job+evento+checkpoint atômicos; worker e recibo continuam locais |
| `EditorialRepository` | `get_snapshot(id)`, `save_revision(id,expected_revision,document,key)`, `append_decision(artifact_hash,revision,decision,key)`; revisão/hash verificados antes de aprovação |
| `ProductionRepository` | `record_result(job,manifest)`, `get(id)`, `append_review(video_hash,decision,key)`; arquivo presente/hash correto e revisão humana independente de sync |
| `CommentRepository` | `append(production,video_hash,event,key)`, `list(cursor)`; IDs estáveis, trilha preservada, sem UPDATE destrutivo; incluir outbox somente após eliminar a janela JSON/banco |
| `SyncJournal` | `enqueue(change)`, `lease_batch(owner,device,limit)`, `ack(op,receipt)`, `record_conflict`, `apply_inbox(batch,cursor)`; limite de tentativas, replay/crash seguros e ack+cursor locais atômicos |
| `RemoteMetadataGateway` | `push_batch(auth_context,changes)`, `pull_page(auth_context,cursor,limit)`; servidor valida identidade, escopo/RLS e schema; cliente não informa um owner confiável por conta própria |

DTO de mudança: `schema_version`, `operation_id` UUID, `device_id` não secreto, `entity_type`, `entity_id` UUID, `base_version`, `payload` permitido, `payload_sha256`, `deleted`/tombstone quando aplicável. `owner_id` vem da identidade validada pelo servidor; em disco, a fila fica segregada por conta/workspace. Recibo: operation_id, hash, versão atribuída e horário do servidor. Mesmo ID/hash retorna mesmo resultado; mesmo ID/hash diferente retorna conflito. Unicidade remota por `(owner_id,operation_id)` e chave natural de versão/entidade, além das FKs/NOT NULL/CHECKs e RLS.

Erros de domínio estáveis: `NotFound`, `Conflict(current_version)`, `ValidationRefused`, `Unauthenticated`, `Forbidden`, `Unavailable(retry_after)`, `QuotaExceeded`, `LocalStorageFailure`. Não expor SQL, senha, DSN, texto privado ou stacktrace ao frontend. Paginação usa cursor opaco vinculado a owner/escopo; ordem remota precisa de log confirmado por servidor. Uma sequence PostgreSQL simples **não garante ordem de commit**: o produtor do feed deve serializar atribuição por workspace na transação (lock de uma linha de cursor por workspace até commit), ou adotar protocolo que não pule transações tardias, com teste concorrente.

Autoridade: banco local decide operação e segurança de execução nesta máquina; servidor decide versão compartilhada confirmada e autorização de acesso remoto. Versão remota recebida gera cópia/revisão, não job, gasto, autorização ou substituição silenciosa do conteúdo usado por render em andamento. Revisão aprovada sincronizada é evidência histórica; sua aplicabilidade exige conferir origem/ator/hash/revisão/política e os gates locais vigentes. Revogação de conta impede sync, sem transformar o modo local em dependência da nuvem.

Logout (R7): remover sessão do cofre, limpar dados puramente cacheados da conta e bloquear sua fila para outra identidade; preservar projetos locais e pendências em workspace segregado, com opção explícita de exportar/descartar, sem envio sob a próxima conta. Definir política de retenção e acesso desses dados antes da Fase D. Limpar cache não significa apagar autorais ou tombstones necessários ao replay.

## 6. Transferência, validação e recuperação propostas

1. **Antes de qualquer dado real:** aprovação da Fase E, allowlist de campos/domínios, owner/membros, retenção de texto e Radar e volumes; backup consistente local e dump remoto em ambiente confiável, hash e restauração já ensaiada. B–D usam só fixtures sintéticas. Snapshot SQLite via backup API; não copiar arquivo ativo sozinho nem anexar WAL antigo.
2. **Manifesto imutável:** versão local/remota/transformador, identificador de lote, contagens por domínio, IDs e hashes canônicos dos campos permitidos, exclusões explícitas; sem caminhos absolutos, mídia ou segredos. UUID hex existentes podem ser preservados como UUID mediante validação; IDs não compatíveis ganham mapa determinístico persistido e namespace de origem. Não regenerar em retry. Hash legado de artefato é preservado como evidência; hash de payload transferido é outro campo, sem recanonizar e invalidar aprovações antigas.
3. **Staging sintético/isolado:** tabelas privadas com RLS e grants verificados antes da primeira escrita, papel de manutenção separado do caminho de usuário. Ordem de grafo não é cópia cega: workflow sem ponteiro atual → job/tentativa/artefato → ponteiro atual/aprovações → scripts → produções → licenças/comentários. Para domínio excluído (jobs técnicos), usar manifesto de proveniência permitido sem copiar payload completo; schema remoto deve expressar referências locais como referências de origem, nunca FKs impossíveis. Dependências cíclicas exigem segunda passagem ou constraints diferíveis revisadas.
4. **Lotes retomáveis:** transação por lote pequeno (começar com 100 entidades/1 MiB e ajustar pela medição); gravar dados + recibo + revisão no mesmo commit remoto. Timeout depois do commit consulta/reenvia operation_id, sem duplicar. Outbox+mudança local no mesmo commit; inbox+cursor+ack no mesmo commit local. Rollback parcial não promove cursor. Nenhuma promessa de exatamente uma execução externa: reenvio do sync não passa por executor.
5. **Validação:** comparar contagens da seleção (não banco inteiro), IDs, hashes e grafo, Unicode/acentos, UTC, JSON null, limites numéricos, unicidade, FKs e CHECKs; recompor o que foi omitido por privacidade como exclusão declarada, nunca como perda silenciosa. Testar duas contas, reatribuição de owner, SELECT/COUNT/UPDATE/DELETE proibidos, views/funções/papéis e insert de FK de outro usuário (FK simples não substitui escopo por owner). Validar R1–R13 e que import/aprovação antiga não vira autorização nova.
6. **Ativação futura gradual:** recurso desligado por padrão, adesão por domínio/projeto, somente após ensaio sintético. Não executar dual-write síncrono SQLite+rede como se fosse transação única. Local permanece gravável. Conflito preserva ambos e requer escolha humana com versão atual; escolha gera nova operação, nunca força UPDATE antigo.
7. **Falhas/recuperação:** retry limitado com jitter/backoff, respeitar 429/Retry-After; 401 pausa sessão, 403 pausa escopo, 409 vai para resolução, 402/cota ou banco read-only pausam envios e alertam; não apagar outbox ao atingir limite. Após crash liberar leases expirados e reenviar mesma operação; apagar/editar cursor manualmente não é recuperação. Registro de diagnóstico contém só IDs/códigos/contagens/durações, sem payload privado.
8. **Retorno:** desativar sync e continuar com base local preservada; reconciliar recibos antes de novo envio; restaurar dump em instância local descartável, comparar hashes/contagens, somente depois planejar troca. Não sobrescrever banco operacional nem descartar mudanças locais posteriores ao snapshot. Reverter schema não é sinônimo de desfazer dados: preferir correção aditiva e restauração em destino novo.

RPO/RTO são metas a aprovar, não resultados: local mantém garantias atuais; remoto pode perder tudo desde o último dump externo validado. Propor dump diário em ambiente confiável (RPO alvo até 24 h) e exercício de recuperação em até 4 h; verificar volume/equipe antes de prometer. Sem rotina ativa e ensaio medido, declarar RPO/RTO desconhecidos. Sem segredo no pacote, nenhum dump automático pelo desktop, nenhum backup gerenciado Free presumido.

## 7. Medição proporcional antes de trocar persistência

Não houve benchmark de produção ou rede nesta Fase A. Medir fixtures com 100/1.000/10.000 entidades textuais, incluindo roteiro grande e 500 comentários; cenários vazios/replay/conflito e filas acumuladas offline. Registrar hardware, SO, versão SQLite/Postgres, índices, build, tamanho serializado e seed. Cinco repetições isoladas por cenário; reportar mediana/p95, CPU/RAM, I/O, tamanho de DB/índices/WAL, duração de transação/lock, consultas por tela, bytes e chamadas por sync, backlog/idade máxima e tempo de recuperação de crash/restauração.

Comparar baseline SQLite com extração de repositório ainda local e depois gateway simulado/remoto sintético; produção offline deve executar sem chamadas externas. O primeiro vídeo continua sendo regressão obrigatória na implementação. Metas propostas: nenhuma espera de rede no commit local; regressão p95 local acima de 10% exige investigação (limiar a calibrar com ruído); limite de lote/timeout ajustado sem manter lock local enquanto espera rede. Não apresentar essas metas como latência demonstrada.

Há medição local já documentada em [migração local](migracao-local.md#custo-dos-checkpoints--medição-sem-otimização): `backend/measure_checkpoints.py` usa pasta temporária, relata custo crescente do texto integral acumulado. Os números históricos não foram repetidos nem extrapolados para PostgreSQL. Migrar esse checkpoint à rede agravaria dependência/volume e não resolve seu custo quadrático.

## 8. Free: limites/custos consultados em 15/11/2026

Valores públicos podem mudar; confirmar painel/termos antes da Fase B. Não foi habilitada cobrança nem consumida API paga. Os limites abaixo não são cota exclusiva reservada ao Hórus.

| Item | Valor/documentação | Impacto |
|---|---|---|
| Preço e capacidade | US$ 0/mês; 500 MB de banco, CPU compartilhada/500 MB RAM; 2 projetos ativos; pausa após 1 semana inativo ([pricing](https://supabase.com/pricing)) | Desenvolvimento; não criar segundo projeto nem keepalive artificial para impedir pausa; produção local continua |
| Rede/Auth/Storage | 5 GB egress + 5 GB egress cacheado, 50.000 usuários ativos/mês, 1 GB Storage, upload até 50 MB ([pricing](https://supabase.com/pricing)) | Não enviar mídia/modelos; planejar delta/paginação e limites por lote; Auth sem transformar conta em requisito local |
| Funções, eventos e logs | 500.000 invocações Edge; Realtime 200 conexões/2 milhões mensagens por mês; logs API/DB 1 dia; sem SLA de uptime ([pricing](https://supabase.com/pricing)) | Backend B tem orçamento operacional; nenhuma necessidade atual de Realtime/polling permanente; auditoria necessária não depende só desses logs |
| Disco e escrita | Free usa disco fixo de 2 GB sem autoscale; read-only a 95% de disco, distinto da franquia de banco de 500 MB; WAL também consome disco; fair-use pode restringir organização com 402 ([database size](https://supabase.com/docs/guides/platform/database-size)) | Medir banco+índices e WAL separadamente, lotes pequenos e alertas propostos a 70/85% das cotas; não relaxar proteção ou contratar upgrade automaticamente |
| Recuperação | Backups diários acessíveis são de planos pagos; para Free a recomendação oficial é exportação CLI e cópia externa ([backups](https://supabase.com/docs/guides/platform/backups)) | R8 exige dump/restauração próprios em ambiente confiável; não habilitar PITR/add-on e não chamar sync de backup |
| Edge, se escolhido para B | 256 MB memória, 2 s CPU por requisição e 150 s de duração máxima Free ([limits](https://supabase.com/docs/guides/functions/limits)) | Somente metadados em lotes curtos; nenhum render/transcrição nessa função; validar pool/conexões no protótipo, sem adotar números de plano pago como Free |

Estimativa de planejamento, não medição: 10.000 entidades × 10 KiB ≈ 98 MiB de payload antes de índices, versões, recibos e overhead; 1.000 mudanças/dia × 10 KiB × 30 ≈ 293 MiB por direção, antes de HTTP e retries. Retenção de muitas versões pode dominar a franquia. Projetar medidores e suspensão segura, nunca exclusão de histórico humano ou aumento automático de plano. Não foram estimados preços de hospedagem externa: provedor/operação do gateway B ainda não foram escolhidos.

## 9. Evidências de schema reconstruído

As seções abaixo são derivadas de AST/DDL versionado e executadas exclusivamente em SQLite `:memory:` vazio. Não importam/inicializam a API, não leem registros operacionais e não certificam PostgreSQL. Incluem colunas, PK/NOT NULL, todos os índices (inclusive implícitos), FKs e SQL final para CHECKs/triggers; `sqlite_sequence` é interna. Nenhum dado de usuário está presente. Resultado: **17 verificações de integridade/FK aprovadas**, 15 tabelas de domínio na main v9 e quatro no Radar; prefixo das migrações v1–v9 idêntico ao da linha retida. Schema v12 reconstruído somente em memória, sem aplicação em arquivo.

### Schema main v9 — catálogo final


#### `approvals`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `workflow_id` | TEXT | True | 0 | `None` |
| `revision` | INTEGER | True | 0 | `None` |
| `artifact_id` | TEXT | True | 0 | `None` |
| `decision` | TEXT | True | 0 | `None` |
| `feedback` | TEXT | True | 0 | `None` |
| `operator_id` | TEXT | True | 0 | `None` |
| `scope` | TEXT | True | 0 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `request` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_approvals_2`: unique=True, origem=u, parcial=False, (workflow_id, idempotency_key).
- `sqlite_autoindex_approvals_1`: unique=True, origem=pk, parcial=False, (id).

FKs: artifact_id → artifacts.id (update=NO ACTION, delete=NO ACTION); workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE approvals (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
            revision INTEGER NOT NULL, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
            decision TEXT NOT NULL CHECK(decision IN ('approve','reject','request_changes')),
            feedback TEXT NOT NULL, operator_id TEXT NOT NULL, scope TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, request TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(workflow_id, idempotency_key));
```


#### `artifacts`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `workflow_id` | TEXT | True | 0 | `None` |
| `revision` | INTEGER | True | 0 | `None` |
| `stage` | TEXT | True | 0 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `attempt_id` | TEXT | True | 0 | `None` |
| `schema_version` | INTEGER | True | 0 | `None` |
| `content` | TEXT | True | 0 | `None` |
| `content_hash` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_artifacts_3`: unique=True, origem=u, parcial=False, (workflow_id, revision, stage).
- `sqlite_autoindex_artifacts_2`: unique=True, origem=u, parcial=False, (job_id).
- `sqlite_autoindex_artifacts_1`: unique=True, origem=pk, parcial=False, (id).

FKs: attempt_id → job_attempts.id (update=NO ACTION, delete=NO ACTION); job_id → jobs.id (update=NO ACTION, delete=NO ACTION); workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
            revision INTEGER NOT NULL, stage TEXT NOT NULL,
            job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
            attempt_id TEXT NOT NULL REFERENCES job_attempts(id),
            schema_version INTEGER NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(workflow_id, revision, stage));
```


#### `authorizations`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `workflow_id` | TEXT | True | 0 | `None` |
| `revision` | INTEGER | True | 0 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `input_artifact_id` | TEXT | True | 0 | `None` |
| `scope` | TEXT | True | 0 | `None` |
| `max_runs` | INTEGER | True | 0 | `None` |
| `operator_id` | TEXT | True | 0 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `request` | TEXT | True | 0 | `None` |
| `consumed_attempt_id` | TEXT | False | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_authorizations_2`: unique=True, origem=u, parcial=False, (workflow_id, idempotency_key).
- `sqlite_autoindex_authorizations_1`: unique=True, origem=pk, parcial=False, (id).

FKs: consumed_attempt_id → job_attempts.id (update=NO ACTION, delete=NO ACTION); input_artifact_id → artifacts.id (update=NO ACTION, delete=NO ACTION); job_id → jobs.id (update=NO ACTION, delete=NO ACTION); workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE authorizations (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
            revision INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
            input_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
            scope TEXT NOT NULL CHECK(scope IN ('cognitive_run')),
            max_runs INTEGER NOT NULL CHECK(max_runs = 1),
            operator_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request TEXT NOT NULL,
            consumed_attempt_id TEXT REFERENCES job_attempts(id), created_at TEXT NOT NULL,
            UNIQUE(workflow_id, idempotency_key));
```


#### `cognitive_runs`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `attempt_id` | TEXT | True | 0 | `None` |
| `provider` | TEXT | True | 0 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `request` | TEXT | True | 0 | `None` |
| `request_hash` | TEXT | True | 0 | `None` |
| `external_ref` | TEXT | False | 0 | `None` |
| `status` | TEXT | True | 0 | `None` |
| `remote_status` | TEXT | False | 0 | `None` |
| `usage` | TEXT | False | 0 | `None` |
| `error` | TEXT | False | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |
| `updated_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `cognitive_runs_job`: unique=False, origem=c, parcial=False, (job_id, created_at).
- `sqlite_autoindex_cognitive_runs_2`: unique=True, origem=u, parcial=False, (idempotency_key).
- `sqlite_autoindex_cognitive_runs_1`: unique=True, origem=pk, parcial=False, (id).

FKs: attempt_id → job_attempts.id (update=NO ACTION, delete=NO ACTION); job_id → jobs.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE cognitive_runs (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
            attempt_id TEXT NOT NULL REFERENCES job_attempts(id),
            provider TEXT NOT NULL CHECK(provider IN ('hermes')),
            idempotency_key TEXT NOT NULL UNIQUE, request TEXT NOT NULL, request_hash TEXT NOT NULL,
            external_ref TEXT,
            status TEXT NOT NULL CHECK(status IN ('submitting','admitted','completed','failed',
                'cancelled','interrupted','unknown','policy_violation','invalid_output')),
            remote_status TEXT, usage TEXT, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
```


#### `events`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `sequence` | INTEGER | False | 1 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `kind` | TEXT | True | 0 | `None` |
| `message` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `events_job`: unique=False, origem=c, parcial=False, (job_id, sequence).

FKs: job_id → jobs.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id), kind TEXT NOT NULL,
            message TEXT NOT NULL, created_at TEXT NOT NULL);
```


#### `job_attempts`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `number` | INTEGER | True | 0 | `None` |
| `status` | TEXT | True | 0 | `None` |
| `executor` | TEXT | True | 0 | `None` |
| `input_hash` | TEXT | True | 0 | `None` |
| `input_artifacts` | TEXT | True | 0 | `None` |
| `started_at` | TEXT | True | 0 | `None` |
| `finished_at` | TEXT | False | 0 | `None` |
| `result` | TEXT | False | 0 | `None` |
| `error` | TEXT | False | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_job_attempts_2`: unique=True, origem=u, parcial=False, (job_id, number).
- `sqlite_autoindex_job_attempts_1`: unique=True, origem=pk, parcial=False, (id).

FKs: job_id → jobs.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE job_attempts (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
            number INTEGER NOT NULL CHECK(number > 0),
            status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted','cancelled')),
            executor TEXT NOT NULL, input_hash TEXT NOT NULL, input_artifacts TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, result TEXT, error TEXT,
            UNIQUE(job_id, number));
```


#### `jobs`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `payload` | TEXT | True | 0 | `None` |
| `status` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |
| `updated_at` | TEXT | True | 0 | `None` |
| `attempt` | INTEGER | True | 0 | `0` |
| `result` | TEXT | False | 0 | `None` |
| `error` | TEXT | False | 0 | `None` |
| `workflow_id` | TEXT | False | 0 | `None` |
| `workflow_revision` | INTEGER | False | 0 | `None` |
| `step_key` | TEXT | False | 0 | `None` |
| `step_index` | INTEGER | False | 0 | `None` |
| `depends_on` | TEXT | False | 0 | `None` |

Índices (nome, único, origem, colunas):

- `workflow_steps`: unique=True, origem=c, parcial=False, (workflow_id, workflow_revision, step_key).
- `sqlite_autoindex_jobs_2`: unique=True, origem=u, parcial=False, (idempotency_key).
- `sqlite_autoindex_jobs_1`: unique=True, origem=pk, parcial=False, (id).

FKs: depends_on → jobs.id (update=NO ACTION, delete=NO ACTION); workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE jobs (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, workflow_id TEXT REFERENCES workflows(id), workflow_revision INTEGER, step_key TEXT, step_index INTEGER, depends_on TEXT REFERENCES jobs(id));
```


#### `license_reviews`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `seq` | INTEGER | False | 1 | `None` |
| `id` | TEXT | True | 0 | `None` |
| `production_id` | TEXT | True | 0 | `None` |
| `asset_id` | TEXT | True | 0 | `None` |
| `asset_sha256` | TEXT | True | 0 | `None` |
| `decision` | TEXT | True | 0 | `None` |
| `license` | TEXT | True | 0 | `None` |
| `evidence` | TEXT | True | 0 | `None` |
| `reviewed_by` | TEXT | True | 0 | `None` |
| `reviewed_at` | TEXT | True | 0 | `None` |
| `supersedes` | TEXT | False | 0 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `request` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `license_reviews_asset`: unique=False, origem=c, parcial=False, (production_id, asset_id, seq).
- `sqlite_autoindex_license_reviews_2`: unique=True, origem=u, parcial=False, (production_id, idempotency_key).
- `sqlite_autoindex_license_reviews_1`: unique=True, origem=u, parcial=False, (id).

FKs: supersedes → license_reviews.id (update=NO ACTION, delete=NO ACTION); production_id → productions.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE license_reviews (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            production_id TEXT NOT NULL REFERENCES productions(id),
            asset_id TEXT NOT NULL, asset_sha256 TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('verified','rejected')),
            license TEXT NOT NULL, evidence TEXT NOT NULL CHECK(length(evidence) BETWEEN 1 AND 2000),
            reviewed_by TEXT NOT NULL CHECK(reviewed_by = 'human'), reviewed_at TEXT NOT NULL,
            supersedes TEXT REFERENCES license_reviews(id),
            idempotency_key TEXT NOT NULL, request TEXT NOT NULL,
            UNIQUE(production_id, idempotency_key));
```


#### `media`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `path` | TEXT | True | 0 | `None` |
| `name` | TEXT | True | 0 | `None` |
| `size` | INTEGER | True | 0 | `None` |
| `modified_ns` | INTEGER | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_media_2`: unique=True, origem=u, parcial=False, (path).
- `sqlite_autoindex_media_1`: unique=True, origem=pk, parcial=False, (id).

FKs: nenhuma.

```sql
CREATE TABLE media (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, created_at TEXT NOT NULL);
```


#### `production_receipts`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `scope` | TEXT | True | 1 | `None` |
| `idempotency_key` | TEXT | True | 2 | `None` |
| `request` | TEXT | True | 0 | `None` |
| `production_id` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_production_receipts_1`: unique=True, origem=pk, parcial=False, (scope, idempotency_key).

FKs: production_id → productions.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE "production_receipts" (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, request TEXT NOT NULL,
            production_id TEXT NOT NULL REFERENCES "productions"(id),
            PRIMARY KEY(scope,idempotency_key));
```


#### `productions`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `script_id` | TEXT | True | 0 | `None` |
| `script_version` | INTEGER | True | 0 | `None` |
| `script_content_hash` | TEXT | True | 0 | `None` |
| `script_review` | TEXT | True | 0 | `None` |
| `job_id` | TEXT | True | 0 | `None` |
| `mode` | TEXT | True | 0 | `None` |
| `plan` | TEXT | True | 0 | `None` |
| `artifacts` | TEXT | True | 0 | `'[]'` |
| `review` | TEXT | False | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_productions_2`: unique=True, origem=u, parcial=False, (job_id).
- `sqlite_autoindex_productions_1`: unique=True, origem=pk, parcial=False, (id).

FKs: job_id → jobs.id (update=NO ACTION, delete=NO ACTION); script_id → scripts.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE "productions" (
            id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id),
            script_version INTEGER NOT NULL, script_content_hash TEXT NOT NULL,
            script_review TEXT NOT NULL, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
            mode TEXT NOT NULL CHECK(mode IN ('simulated','local_demo','local_pilot')),
            plan TEXT NOT NULL, artifacts TEXT NOT NULL DEFAULT '[]', review TEXT,
            created_at TEXT NOT NULL);
```


#### `script_receipts`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `scope` | TEXT | True | 1 | `None` |
| `idempotency_key` | TEXT | True | 2 | `None` |
| `request` | TEXT | True | 0 | `None` |
| `response` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_script_receipts_1`: unique=True, origem=pk, parcial=False, (scope, idempotency_key).

FKs: nenhuma.

```sql
CREATE TABLE script_receipts (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, request TEXT NOT NULL,
            response TEXT NOT NULL, PRIMARY KEY(scope, idempotency_key));
```


#### `scripts`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `workflow_id` | TEXT | True | 0 | `None` |
| `workflow_revision` | INTEGER | True | 0 | `None` |
| `proposal_artifact_id` | TEXT | True | 0 | `None` |
| `proposal_content_hash` | TEXT | True | 0 | `None` |
| `version` | INTEGER | True | 0 | `None` |
| `status` | TEXT | True | 0 | `None` |
| `document` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |
| `review` | TEXT | False | 0 | `None` |
| `origin` | TEXT | True | 0 | `'simulated'` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_scripts_2`: unique=True, origem=u, parcial=False, (workflow_id, version).
- `sqlite_autoindex_scripts_1`: unique=True, origem=pk, parcial=False, (id).

FKs: proposal_artifact_id → artifacts.id (update=NO ACTION, delete=NO ACTION); workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE scripts (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(id),
            workflow_revision INTEGER NOT NULL, proposal_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
            proposal_content_hash TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
            status TEXT NOT NULL CHECK(status IN ('awaiting_review','approved','rejected','changes_requested')),
            document TEXT NOT NULL, created_at TEXT NOT NULL, review TEXT, origin TEXT NOT NULL DEFAULT 'simulated'
            CHECK(origin IN ('simulated','human')),
            UNIQUE(workflow_id, version));
```


#### `workflow_events`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `sequence` | INTEGER | False | 1 | `None` |
| `workflow_id` | TEXT | True | 0 | `None` |
| `revision` | INTEGER | True | 0 | `None` |
| `kind` | TEXT | True | 0 | `None` |
| `data` | TEXT | True | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `workflow_event_cursor`: unique=False, origem=c, parcial=False, (workflow_id, sequence).

FKs: workflow_id → workflows.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE workflow_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL REFERENCES workflows(id), revision INTEGER NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
```


#### `workflows`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `idempotency_key` | TEXT | True | 0 | `None` |
| `payload` | TEXT | True | 0 | `None` |
| `revision` | INTEGER | True | 0 | `None` |
| `status` | TEXT | True | 0 | `None` |
| `feedback` | TEXT | True | 0 | `''` |
| `current_artifact_id` | TEXT | False | 0 | `None` |
| `created_at` | TEXT | True | 0 | `None` |
| `updated_at` | TEXT | True | 0 | `None` |
| `executor` | TEXT | True | 0 | `'simulated'` |
| `cognitive_client` | TEXT | True | 0 | `'live'` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_workflows_2`: unique=True, origem=u, parcial=False, (idempotency_key).
- `sqlite_autoindex_workflows_1`: unique=True, origem=pk, parcial=False, (id).

FKs: current_artifact_id → artifacts.id (update=NO ACTION, delete=NO ACTION).

```sql
CREATE TABLE workflows (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
            status TEXT NOT NULL CHECK(status IN ('queued','running','awaiting_approval',
                'approved','rejected','cancel_requested','cancelled','interrupted','failed')),
            feedback TEXT NOT NULL DEFAULT '', current_artifact_id TEXT REFERENCES artifacts(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, executor TEXT NOT NULL DEFAULT 'simulated'
            CHECK(executor IN ('simulated','hermes')), cognitive_client TEXT NOT NULL DEFAULT 'live'
            CHECK(cognitive_client IN ('live','simulated')));
```


Trigger `license_reviews_no_delete`:

```sql
CREATE TRIGGER license_reviews_no_delete BEFORE DELETE ON license_reviews
            BEGIN SELECT RAISE(ABORT, 'license_reviews é append-only'); END;
```


Trigger `license_reviews_no_update`:

```sql
CREATE TRIGGER license_reviews_no_update BEFORE UPDATE ON license_reviews
            BEGIN SELECT RAISE(ABORT, 'license_reviews é append-only'); END;
```


### Complemento retido v10–v12

`PRAGMA table_info(scripts)` acrescenta `generator TEXT`, anulável, default NULL; CHECK conforme tabela de migrações. O schema v12 em memória tem 17 tabelas de domínio.


`human_checkpoints_lookup`:

```sql
CREATE INDEX human_checkpoints_lookup ON human_checkpoints(action, subject, seq);
```


`transmissions_run`:

```sql
CREATE INDEX transmissions_run ON transmissions(run_id, seq);
```


`human_checkpoints`:

```sql
CREATE TABLE human_checkpoints (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL CHECK(action IN ('hermes_run','hermes_script','paid_narration','publish')),
            subject TEXT NOT NULL CHECK(length(subject) = 64),
            description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 200),
            approved_by TEXT NOT NULL CHECK(approved_by = 'human'), operator_id TEXT NOT NULL,
            approved_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            limit_amount TEXT, currency TEXT CHECK(currency IS NULL OR length(currency) = 3),
            consumed_at TEXT, consumed_ref TEXT, idempotency_key TEXT UNIQUE, request TEXT NOT NULL,
            CHECK((limit_amount IS NULL) = (currency IS NULL)));
```


`transmissions`:

```sql
CREATE TABLE transmissions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, at TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('submit','outcome','gate_refusal','probe')),
            stage TEXT NOT NULL, workflow_id TEXT, job_id TEXT, attempt_id TEXT, run_id TEXT,
            destination TEXT, provider TEXT, model TEXT,
            envelope_bytes INTEGER CHECK(envelope_bytes IS NULL OR envelope_bytes >= 0),
            envelope_sha256 TEXT CHECK(envelope_sha256 IS NULL OR length(envelope_sha256) = 64),
            authorization_id TEXT,
            result TEXT NOT NULL CHECK(result IN ('attempted','sent','error','unknown','refused_gate','completed',
                'failed','cancelled','interrupted','invalid_output','policy_violation')),
            detail TEXT CHECK(detail IS NULL OR length(detail) <= 120),
            tokens_total INTEGER CHECK(tokens_total IS NULL OR tokens_total >= 0), cost_usd TEXT);
```


`human_checkpoints_consume_once`:

```sql
CREATE TRIGGER human_checkpoints_consume_once BEFORE UPDATE ON human_checkpoints
            WHEN NOT (OLD.consumed_at IS NULL AND NEW.consumed_at IS NOT NULL AND NEW.id = OLD.id AND NEW.seq = OLD.seq
                AND NEW.action = OLD.action AND NEW.subject = OLD.subject AND NEW.description = OLD.description
                AND NEW.approved_by = OLD.approved_by AND NEW.operator_id = OLD.operator_id
                AND NEW.approved_at = OLD.approved_at AND NEW.expires_at = OLD.expires_at
                AND NEW.limit_amount IS OLD.limit_amount AND NEW.currency IS OLD.currency
                AND NEW.request = OLD.request AND NEW.idempotency_key IS OLD.idempotency_key)
            BEGIN SELECT RAISE(ABORT, 'human_checkpoints só aceita o consumo único'); END;
```


`human_checkpoints_no_delete`:

```sql
CREATE TRIGGER human_checkpoints_no_delete BEFORE DELETE ON human_checkpoints
            BEGIN SELECT RAISE(ABORT, 'human_checkpoints é append-only'); END;
```


`transmissions_no_delete`:

```sql
CREATE TRIGGER transmissions_no_delete BEFORE DELETE ON transmissions
            BEGIN SELECT RAISE(ABORT, 'transmissions é append-only'); END;
```


`transmissions_no_update`:

```sql
CREATE TRIGGER transmissions_no_update BEFORE UPDATE ON transmissions
            BEGIN SELECT RAISE(ABORT, 'transmissions é append-only'); END;
```


### Schema Radar — catálogo reconstruído


#### `radar_blocks`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `bucket` | TEXT | False | 1 | `None` |
| `until` | REAL | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_radar_blocks_1`: unique=True, origem=pk, parcial=False, (bucket).

FKs: nenhuma.

```sql
CREATE TABLE radar_blocks(bucket TEXT PRIMARY KEY, until REAL NOT NULL);
```


#### `radar_cache`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `key` | TEXT | False | 1 | `None` |
| `fetched` | TEXT | True | 0 | `None` |
| `expires` | REAL | True | 0 | `None` |
| `payload` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_radar_cache_1`: unique=True, origem=pk, parcial=False, (key).

FKs: nenhuma.

```sql
CREATE TABLE radar_cache(key TEXT PRIMARY KEY, fetched TEXT NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL);
```


#### `radar_datasets`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | True | 1 | `None` |
| `revision` | INTEGER | True | 2 | `None` |
| `expires` | REAL | True | 0 | `None` |
| `payload` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_radar_datasets_1`: unique=True, origem=pk, parcial=False, (id, revision).

FKs: nenhuma.

```sql
CREATE TABLE radar_datasets(id TEXT NOT NULL, revision INTEGER NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,revision));
```


#### `radar_usage`

| Coluna | Tipo | NOT NULL declarado | PK (posição) | Default |
|---|---|---|---|---|
| `id` | TEXT | False | 1 | `None` |
| `at` | REAL | True | 0 | `None` |
| `bucket` | TEXT | True | 0 | `None` |

Índices (nome, único, origem, colunas):

- `sqlite_autoindex_radar_usage_1`: unique=True, origem=pk, parcial=False, (id).

FKs: nenhuma.

```sql
CREATE TABLE radar_usage(id TEXT PRIMARY KEY, at REAL NOT NULL, bucket TEXT NOT NULL);
```


Nota: em SQLite, `TEXT PRIMARY KEY` pode reportar NOT NULL falso e aceitar NULL em certas tabelas rowid; não presumir validação equivalente à PK PostgreSQL. FKs não ganham índices automaticamente no PostgreSQL; revisar índices por owner e consulta, além dos existentes.
