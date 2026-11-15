# Segurança dos dados locais (SQLite operacional)

Escopo: o banco operacional `horus.sqlite3` (jobs, workflows, artefatos, aprovações, execuções cognitivas) e os backups de migração criados ao lado dele. O estado do Radar (`radar/radar-youtube.sqlite3`) já usa `secure_delete=ON` e tem limpeza própria. Nada aqui usa criptografia própria: são mecanismos consolidados do SQLite e recomendações para o sistema operacional.

## O que é garantido

| Mecanismo | Efeito | Onde |
|---|---|---|
| `PRAGMA secure_delete=ON` em **toda** conexão do banco operacional | Páginas e células liberadas por `DELETE`/`UPDATE` (inclusive páginas de *overflow* de valores grandes) são sobrescritas com zeros no momento em que são liberadas, também dentro das páginas ainda em uso | `Store.connect()` em `storage.py`. É um pragma **por conexão**; `migrations.py` abre a própria conexão (só migrações aditivas, sem remoção de dados) e não o define |
| `wal_checkpoint(TRUNCATE)` após a retenção | O banco roda em `journal_mode=WAL`. Os quadros antigos permanecem no `-wal` até um checkpoint, e mesmo um checkpoint comum (`PASSIVE`) copia para o `.sqlite3` sem esvaziar o `-wal`. `TRUNCATE` copia tudo e reduz o `-wal` a 0 byte | `Store.checkpoint()`; chamado por `run_retention` quando ao menos um workflow foi redigido |
| Checkpoint pendente é repetido | Leitor ativo, banco bloqueado ou erro **não** quebram o ciclo: o resultado fica no relatório e o ciclo seguinte (a cada 6 h) repete o checkpoint mesmo sem novas redações | `RetentionReport.checkpoint_pending`, `JobManager.checkpoint_pending` |
| Resultado exposto sem conteúdo | `status` (`ok`, `busy`, `error`), `wal_frames`, `checkpointed_frames`, `wal_bytes`, `freelist_pages`, `page_count` | `RetentionReport.checkpoint`; em `JobManager.retention_status["checkpoint"]` (→ `GET /v1/security/status`, `radar.automatic_cleanup.checkpoint`) |
| Inventário e *dry-run* dos backups de migração | Lista o que a política removeria e por quê, sinaliza backups com cópias do Radar; leitura estritamente somente leitura | `backups.py` |

Medições nos testes (`backend/tests/test_sqlite_hardening.py`; marcador único em ~60 KB–190 KB, páginas de 4 KiB):

| Cenário | Sem `secure_delete` | Com `secure_delete=ON` |
|---|---|---|
| `UPDATE` de valor grande por um curto + `TRUNCATE` | marcador permanece no `.sqlite3` (≈ 9 900 ocorrências em 46 páginas livres) | 0 ocorrências; 46 páginas livres **zeradas** |
| `DELETE` + `TRUNCATE` | idem | 0 |
| Ciclo de retenção em workflow Radar fictício (300 itens) | 886 ocorrências no `.sqlite3`, 31 de 66 páginas livres | 0 no `.sqlite3` e 0 no `-wal`; as mesmas 31 páginas livres, zeradas |
| Antes do checkpoint, `-wal` | 890 ocorrências (quadros originais) | 890 (o `secure_delete` não reescreve quadros antigos) |
| Depois de `PASSIVE` | 890 ocorrências ainda no `-wal` | idem |
| Depois de `TRUNCATE` | `-wal` com 0 byte | `-wal` com 0 byte |

Conclusão: `secure_delete` impede o resíduo **no arquivo principal**; o checkpoint `TRUNCATE` elimina o resíduo **no WAL**. Um sem o outro não basta. Fechar a última conexão do banco também faz o SQLite fazer checkpoint e apagar o `-wal`, mas o backend abre conexões curtas que podem se sobrepor entre threads e processos auxiliares, então não se deve depender disso.

## O que NÃO é garantido

- **Páginas livres antigas.** `secure_delete` só vale para o que for liberado *depois* que o pragma está ativo. Bancos que já passaram por redações/remoções com a versão anterior têm resíduo na lista de páginas livres que o novo comportamento **não limpa** (teste: o marcador continua no arquivo após ligar `secure_delete` e rodar a retenção; somem só com `VACUUM`). Ver *VACUUM* abaixo.
- **SSD, TRIM e *wear leveling*.** Sobrescrever com zeros no nível do SQLite não garante que a célula flash física antiga tenha sido apagada: o controlador pode gravar o novo conteúdo em outro bloco e manter o antigo até reciclá-lo. O mesmo vale para sistemas de arquivos com cópia-na-escrita. Só criptografia de disco torna esse resíduo inútil.
- **Backups de migração** (`horus.backup-v<N>-<token>.sqlite3`). São cópias completas feitas pela API de *backup* do SQLite antes de cada migração; **copiam inclusive páginas livres** do banco de origem e não são alcançados pela redação nem pelo checkpoint. Um backup feito de um banco que já tinha cópias do Radar mantém essas cópias, em texto, por tempo indefinido. A remoção está **desligada por padrão** (ver política abaixo).
- **Cópias fora do banco:** sombra/pontos de restauração e histórico de arquivos do Windows, OneDrive e qualquer pasta sincronizada ou com backup (Hórus com `data_dir` dentro delas), exportações feitas pelo operador, arquivos temporários, `pagefile.sys`/hibernação, logs de processos externos e a cópia mantida pelo **Hermes real** (sessões, memória, logs). A redação do Hórus não alcança nenhuma delas.
- **Dados em memória e processos em execução**, e arquivos copiados por terceiros enquanto os dados ainda existiam.
- O `-shm` não contém dados das linhas, mas faz parte do conjunto de arquivos do banco.

## Recomendações ao operador (fora do código)

1. **BitLocker** (ou equivalente) no volume que contém a pasta de dados do Hórus. É a medida que cobre SSD, páginas livres antigas, backups, arquivos temporários e hibernação. Sem ela, as garantias acima são de melhor esforço.
2. Manter a pasta de dados **fora** de OneDrive/pastas sincronizadas e fora de rotinas de backup que retenham versões antigas; revisar a proteção do sistema (pontos de restauração) para esse volume.
3. Tratar exportações e a pasta do Hermes real como dados sujeitos à mesma política.
4. Executar, **uma vez**, a limpeza de resíduo antigo descrita em *VACUUM* se o banco já guardou dados do Radar antes desta versão.

## Páginas livres, `auto_vacuum` e `VACUUM` (investigação e recomendação)

| Opção | Comportamento medido | Recomendação |
|---|---|---|
| `secure_delete=ON` | Zera o que é liberado. Custo pequeno de escrita extra (as páginas zeradas passam pelo WAL: 46 páginas = 197 KB num teste de 190 KB). Não toca resíduo anterior | **Aplicado** |
| `auto_vacuum=FULL` | Devolve páginas livres ao sistema de arquivos a cada *commit* (arquivo de 200 704 → 12 288 bytes), mas **sem** `secure_delete` restos do valor antigo continuam dentro das páginas ainda em uso (92 ocorrências no teste). Só se aplica a bancos novos ou depois de um `VACUUM`; muda o formato do arquivo | **Não aplicado.** Reduz tamanho, não substitui `secure_delete` |
| `VACUUM` | Reescreve o banco inteiro: elimina por completo o resíduo antigo da lista de páginas livres (marcador 9 947 → 0). Exige o **dobro do espaço** (no modo WAL o `-wal` cresce até ≈ o tamanho do banco: 41,3 MB de WAL para um banco de 41,1 MB, que terminou com 20,5 MB), segura o bloqueio de escrita durante toda a operação (≈ 0,24 s para 41 MB nos testes; cresce linearmente) e deve ser seguido de `wal_checkpoint(TRUNCATE)`. Não altera backups já existentes | **Não automático.** Execução manual, uma vez, com o backend parado e espaço livre ≥ 2× o banco, após ligar `secure_delete`; depois `PRAGMA wal_checkpoint(TRUNCATE)` |
| `VACUUM INTO 'novo.sqlite3'` | Gera uma cópia compacta, sem páginas livres e sem alterar o original; útil para criar um backup limpo | Alternativa para exportar/arquivar sem resíduo |

Decisão do idealizador pendente: executar (ou não) o `VACUUM` único nos bancos que já contêm dados do Radar redigidos antes desta versão. O backend não o executa por conta própria.

## Backups de migração: política

Os backups `horus.backup-v<versão>-<token>.sqlite3` ficam na pasta do banco. `backups.py` oferece inventário e política; **nada é removido automaticamente** e o ciclo de retenção nunca os acessa.

### Verificar e ensaiar a restauração

Com o aplicativo fechado e o SQLite checkpointado, execute:

```powershell
python -m horus_backend.backups verify "caminho/horus.sqlite3"
python -m horus_backend.backups verify "caminho/horus.sqlite3" --json
python -m horus_backend.backups restore "caminho/horus.backup-v9-TOKEN.sqlite3" --to "caminho/horus-restaurado.sqlite3"
```

`verify` lê cada backup e o banco atual em modo imutável/somente leitura, sem criar
sidecars: executa `integrity_check`, informa `user_version`, contagens por tabela
e diferenças frente ao atual. WAL/journal não vazio bloqueia a leitura para não
ignorar transações pendentes; feche o app e conclua o checkpoint antes de repetir.
Diferenças de contagem e tabelas entre versões são informativas, não corrupção.
A saída padrão contém resumo e JSON; `--json` em ambos os comandos emite apenas JSON.
Código de saída 0 indica sucesso, 1 indica falha de verificação/operação e 2 erro de uso.

`restore` exige destino novo, recusa arquivo ou sidecar existente e verifica a
integridade, versão e contagens após a cópia SQLite. Não migra nem modifica a
origem, não substitui o banco operacional e não apaga arquivos. Se houver falha
após criar o destino, preserve a cópia incompleta para análise e escolha outro nome.
Com o app **fechado**, guarde o `horus.sqlite3` atual e seus sidecars juntos em uma
pasta de segurança. Copie manualmente o arquivo restaurado para `horus.sqlite3`
na pasta de dados configurada, sem misturar os sidecars antigos; a abertura pelo
backend pode migrar essa cópia.
Confirme os dados no app antes de qualquer troca definitiva; integridade SQLite e
contagens não provam a disponibilidade das mídias externas referenciadas no banco.

### Inventário e retenção

- **Inventário:** nome, versão de origem, tamanho, idade, e, abrindo o backup somente leitura (`mode=ro&immutable=1`, sem criar `-wal`/`-shm`), `json_extract(payload,'$.radar')` para contar workflows com cópia do Radar (total e ainda não redigidos). Backup ilegível mostra `?`.
- **Regras, na ordem:**
  1. versão do backup **maior** que o schema atual: mantido sempre;
  2. o backup da migração **mais recente** (ou o único): mantido sempre, mesmo vencido;
  3. sem prazo configurado: tudo mantido;
  4. mais novo que `N` dias: mantido; mais antigo: candidato à remoção.
- **Backups com Radar** são sinalizados; o mantido por 1–3 com Radar não redigido fica em `needs_operator_decision`: o prazo de 28 dias do Radar já pode ter passado, e só o operador decide remover ou arquivar (por exemplo, apagar o backup mais recente depois de confirmar que a migração foi bem-sucedida).
- **Remoção real:** só com `HORUS_BACKUP_RETENTION_DAYS=<inteiro ≥ 1>` definida **e** chamada explícita (`remove_backups(..., dry_run=False)` ou `--apply`). Valor ausente, vazio, não numérico ou `< 1` mantém tudo. Apaga também `-wal`/`-shm`/`-journal` do backup removido; erro em um arquivo (por exemplo, em uso) é registrado e os demais seguem.
- **Uso pelo operador:**

  ```powershell
  # lista e justifica (dry-run); não altera nada
  & backend/.venv/Scripts/python.exe -m horus_backend.backups "$env:LOCALAPPDATA/Horus/horus.sqlite3"
  # remoção, somente com prazo explícito e --apply
  $env:HORUS_BACKUP_RETENTION_DAYS = "30"
  & backend/.venv/Scripts/python.exe -m horus_backend.backups "$env:LOCALAPPDATA/Horus/horus.sqlite3" --apply
  ```

  Confirme o caminho real da pasta de dados antes de executar. Os testes usam apenas `tmp_path`.

## Validação

`backend/tests/test_sqlite_hardening.py` cobre: `secure_delete` em toda conexão; marcador ausente dos bytes do `.sqlite3` e do `-wal` após retenção + checkpoint; controle sem `secure_delete` (marcador permanece); `UPDATE`/`DELETE` de valores com *overflow*; `-wal` antes/depois de `PASSIVE`/`TRUNCATE`; checkpoint com leitor ativo (`busy`, ciclo íntegro, repetição no ciclo seguinte); erro de checkpoint; `dry_run` sem checkpoint; `integrity_check`/`foreign_key_check` e contagens de linhas; reabertura após falha no meio da redação e recuperação de cópia dos arquivos com transação aberta; resíduo anterior exigindo `VACUUM`; `auto_vacuum`; custo do `VACUUM`; inventário, *dry-run* e remoção de backups (incluindo preservação do último/único e dos de versão futura). Todos em `tmp_path`, com dados fictícios e relógio injetado. Isso valida o comportamento do SQLite neste ambiente; não prova apagamento físico em SSD.

## Autenticação de endpoints e rede local (HOR-127)

`backend/tests/test_endpoint_auth.py` percorre todas as rotas registradas (`app.routes`, inclusive routers incluídos) e exige 401 sem token ou com token errado; uma rota nova sem autenticação faz o teste falhar, e rota que não seja HTTP comum também (revisão obrigatória).

- **Única rota sem Bearer:** `GET /v1/media/{id}/stream`, autorizada por ticket HMAC (token local, id da mídia e expiração), porque o elemento de vídeo não envia cabeçalhos. `/health` e `/v1/security/status` exigem token; não há `/docs` nem OpenAPI.
- **Rede:** o servidor escuta somente em `127.0.0.1` (testado com o processo real e com os endereços de LAN do computador). `Host` precisa ser `127.0.0.1` ou `localhost` (com porta opcional), contra DNS rebinding; `Origin` precisa estar na lista fixa (mais uma origem loopback opcional por variável de ambiente); CORS nunca devolve `*`.
- **Token:** mínimo de 24 caracteres, comparado com `secrets.compare_digest` sobre bytes (também o ticket). Correções desta revisão: cabeçalho/ticket não ASCII respondia HTTP 500 (agora 401) e `Host` com `usuario@` era aceito (agora 403); `GET` de mídia recusa arquivo trocado por link simbólico depois do registro.
- **Mídia e artefatos:** ids de mídia são buscados no banco (sem caminho); o registro resolve links e exige extensão audiovisual; pasta ignora links e subpastas; artefatos de produção passam por `confined` (sem `..`, absoluto, `\`, `:` nem links).
- **Limitações conhecidas:** (1) **limite de corpo implementado** (`body_limit.py`, ASGI interno à autenticação): padrão 2 MiB, contado em bytes reais em streaming (inclusive chunked e `Content-Length` mentiroso), resposta 413 em pt-BR sem executar o manipulador; tetos maiores só onde o modelo já admite texto grande: `POST /v1/transcriptions/save` 64 MiB (20.000.000 caracteres × até 3 bytes UTF-8) e `/v1/narration/*` 4 MiB (1.000.000 caracteres). Corpo sem token continua 401 sem ser lido. Limite restante: o corpo aceito (até o teto da rota) ainda é lido inteiro em memória para validar; (2) o token vive em variável de ambiente do processo e no cabeçalho; qualquer processo do mesmo usuário que o conheça é confiável; (3) neste ambiente Windows os testes com `symlink` real foram pulados (sem privilégio); a recusa é coberta por stub e pela análise de `confined`; (4) `Host` ausente (HTTP/1.0) cai no endereço da conexão.

## Subprocessos: contenção atual e avaliação de restrições de SO (HOR-124)

Contenção **implementada**: todo subprocesso do backend usa argv em lista (sem shell), `processes.OwnedProcess` (assíncrono) ou `processes.run_sync` (síncrono); ambiente por allowlist (padrão mínimo quando o chamador não define `env`; jobs e produção usam allowlists próprias); `cwd` controlado; prazo; saída em memória/arquivo limitada (16 MiB por fluxo; o excedente é descartado); stdin fechado; árvore inteira encerrada por Job Object com `KILL_ON_JOB_CLOSE` no Windows (inclusive se o backend morrer de forma abrupta) e por grupo de processos no POSIX. Os módulos `production_worker.py` e `transcriber.py` criam netos, mas rodam dentro do worker já sob o Job. `test_process_sandbox.py` falha se surgir `shell=True`, `os.system` ou subprocesso cru fora desses módulos. Limite conhecido: o processo é atribuído ao Job logo após o `CreateProcess`; um filho criado nessa janela de microssegundos pelo próprio executável não é coberto (não observado; fechar exigiria `CREATE_SUSPENDED`, fora do alcance do `asyncio`).

Avaliação curta (não aplicada, decisão do idealizador): **AppContainer/token restrito** limitaria arquivos e rede dos workers (FFmpeg, Kokoro, render) além do que o Job Object faz (que só limita vida e árvore), mas exige empacotamento próprio, ACLs nos diretórios de dados e testes por ferramenta (Chromium do render e GPU/CUDA do Whisper costumam quebrar em AppContainer); **usuário local restrito** exige conta e credenciais extras no instalador. Recomendação: tratar junto ao empacotamento (HOR-100) como item opcional pós-piloto; até lá, Job Object + ambiente mínimo + confinamento de caminhos (HOR-123).

## Telemetria, exportação e exclusão dos dados locais (HOR-117)

**Telemetria: nenhuma.** `test_telemetry_and_local_data.py` falha se um lockfile (Python, npm, Cargo) trouxer biblioteca de telemetria, relatório de falhas ou atualização automática; se o Tauri ganhar plugin além de `dialog` ou o `connect-src` deixar de ser loopback; se o código versionado citar hosts de analytics; ou se um subprocesso (`OwnedProcess`/`run_sync`) começar sem `HF_HUB_DISABLE_TELEMETRY`, `DO_NOT_TRACK` e afins (`processes.TELEMETRY_OFF`, aplicado inclusive sobre o ambiente do operador). Kokoro e alinhamento já forçam `HF_HUB_OFFLINE`. **Storybook** (`../horus-storybook`, outro repositório): `.storybook/main.ts` tem `core: { disableTelemetry: true }` (conferido em 14/11/2026; o teste confere quando `HORUS_STORYBOOK_DIR` aponta para ele); nenhuma ação para o Antigravity. Limite: o teste é estático/de ambiente; não observa tráfego de rede de binários de terceiros (cobertura de rede em `test_network_inventory.py` e [política de destinos](politica-destinos-e-ameacas.md)).

**Exportar meus dados (implementado, local, sem endpoint).** Não havia equivalente (`exports.py` empacota um vídeo; `backups.py` só verifica/restaura o banco). Novo `local_data_export.py`, somente leitura da origem:

```powershell
& backend/.venv/Scripts/python.exe -m horus_backend.local_data_export export "<pasta-de-dados>" --to "<pasta-segura>/horus-dados.zip"
& backend/.venv/Scripts/python.exe -m horus_backend.local_data_export verify "<pasta-segura>/horus-dados.zip"
```

O pacote traz cópia consistente (API de backup do SQLite, funciona com o app aberto) do banco e do Radar, `narration-preference.json`, os `productions/<id>/*.json`, os comentários de revisão (`production-comments/<id>.json`, HOR-088) e o estado de leitura das notificações (`notifications-state.json`, ambos validados em estrutura na exportação e na verificação e restaurados nos mesmos caminhos), com `manifest.json` (SHA-256, tamanho, contagem de tabelas, itens excluídos). **Nunca** inclui `integrations/` (token do Hermes), `models/`, mídia, `exports/`, JSON com formato de credencial nem links. `verify` confere hashes, nomes seguros e integridade dos bancos. Recusa sobrescrever e gravar dentro da pasta de dados. O pacote tem dados privados (`contains_private_data=true`): guarde-o como o banco (BitLocker, fora de pastas sincronizadas). **Restaurar (HOR-093, CLI):** `python -m horus_backend.local_data_export restore "<pacote.zip>" --to "<pasta-nova-ou-vazia>"`. Verifica o pacote por inteiro antes de escrever (hashes, nomes, integridade, somente nomes do formato do Hórus, sem links, sem formato de credencial), recusa destino não vazio/arquivo/link e banco de schema mais novo que o aplicativo, grava numa pasta temporária irmã e só então renomeia para o destino (nada parcial). Não migra o banco e não restaura credenciais (reconfigure Hermes e tokens), mídia nem `exports/`: o pacote não os contém. Depois, aponte `HORUS_DATA_DIR` para a pasta restaurada. Prova ponta a ponta em `test_local_data_restore.py`. `backups restore` segue válido para restaurar só o banco.

**Apagar dados locais — fatia segura implementada (escopos produção e importação).** Código: `backend/src/horus_backend/data_deletion.py`; testes: `backend/tests/test_data_deletion.py` (somente `HORUS_DATA_DIR` temporário e dados sintéticos). Endpoints aditivos (autenticados):

| Rota | Função |
|---|---|
| `POST /v1/data-deletion/preview` `{scope: production\|import, id}` | Somente leitura. Devolve `plan_id`, `intent_token` (uso único, 5 min), `confirmation_phrase` (`APAGAR producao <id8>` / `APAGAR importacao <id8>`), `items` (pasta da produção, exports, comentários ou importação, com caminho relativo, arquivos e bytes), `db_kept` (linhas do banco que **permanecem** e por quê, incluindo `license_reviews` append-only), `dependencies` (roteiro/workflow, não apagados), `blockers` e `can_execute` |
| `POST /v1/data-deletion/execute` `{plan_id, intent_token, phrase}` | Exige frase exata e token; recusa (409) com **qualquer job ativo** (também revalidado depois do backup), plano já usado/expirado, arquivos alterados desde a prévia, link/junção no alvo ou auditoria adulterada. Gera **automaticamente** o pacote local em `<pasta-de-dados>-backups/pre-exclusao-<data>-<id>.zip` (fora da pasta de dados), roda `verify_export` e só então prossegue; grava `started`, move (rename atômico, com desfazer em falha) os arquivos para `<pasta-de-dados>/.quarentena/<audit_id>/` e grava `completed` |
| `POST /v1/data-deletion/{audit_id}/restore` | Devolve da quarentena conferindo o resumo dos arquivos (caminho relativo + tamanho); recusa se a quarentena foi alterada ou se o destino já existe; uma vez por exclusão |
| `GET /v1/data-deletion/audit?limit&before` | Eventos mais recentes e `chain_ok` |

Decisões e limites: (1) **o banco não é alterado** (preserva trilha, licenças append-only e chaves estrangeiras; texto de roteiro/plano continua nas linhas do banco: a exclusão cobre arquivos, não o conteúdo do SQLite); (2) o pacote local **não leva vídeos, mídia nem exports**, então quem preserva esses arquivos é a quarentena, recuperável até a purga; **a purga definitiva da quarentena não está implementada** (tarefa própria, com a mesma frase) e os pacotes de pré-exclusão **nunca são apagados automaticamente**; (3) a produção excluída aparece com `local_data.state = "deleted"` (derivado da auditoria, sem migração) e `POST export`, `GET export file`, revisão, artefatos e comentários novos são recusados (409) até a restauração; importações excluídas somem da listagem; (4) auditoria em `<pasta-de-dados>/deletion-audit.json`: JSON atômico, eventos `started/completed/failed/restored` encadeados por SHA-256 (`prev_hash`/`hash`), sem conteúdo dos arquivos; uma cadeia adulterada bloqueia novas exclusões; (5) fora desta fatia: escopos Radar, transcrições/mídia registrada e "tudo" (B–D), exclusão remota (aguarda a decisão B/C do HOR-137), `VACUUM`/checkpoint do banco e cópias fora do Hórus (OneDrive, histórico do Windows, Hermes real). Ações nunca executadas contra dados reais pelos testes.

**Desenho original (referência):**

| Ponto | Proposta |
|---|---|
| Escopos | (A) uma produção (pasta `productions/<id>` + linhas ligadas); (B) estado do Radar; (C) transcrições/mídia registrada; (D) tudo (banco, Radar, produções, exports, preferências), **exceto** `integrations/` e `models/`, que têm exclusão própria e explícita |
| Pré-condição | O app só executa com o worker parado (sem job em andamento) e **backup obrigatório antes**: `local_data_export` verificado com `verify` (a exclusão recusa se o pacote não existir/validar fora da pasta de dados) |
| Confirmação dupla | 1ª: tela lista escopo, contagens e tamanho em disco e pede o pacote de backup; 2ª: o usuário digita a palavra `APAGAR` e o nome do escopo; a API exige `confirm` + `token de intenção` de uso único (expira em 60 s) emitido na etapa 1 |
| Execução | Plano gravado em um registro append-only (o que será removido, hashes), remoção item a item com relatório; falha parcial deixa o app em estado consistente e informa o que restou; nada é removido por nome/padrão fora da pasta de dados (usa `file_access`/`confined`) |
| Resíduos | Após apagar o banco: `VACUUM`+`wal_checkpoint(TRUNCATE)` (ver tabela acima) e aviso de que backups de migração, cópias sincronizadas e dados do Hermes real **não** são alcançados; `backups.py --apply` segue separado |
| Fora do escopo | Apagar dados do Hermes real, do OneDrive/histórico do Windows e arquivos exportados pelo usuário |
| Decisões do usuário | Escopos desejados (A–D)? Backup obrigatório mesmo no escopo A? Exigir reinício do app após D? Manter o token de intenção ou só a digitação? |

## Inventário de segredos e dados sensíveis, em repouso e em trânsito (HOR-114)

**Estado: inventário verificado no código e em testes; nenhuma mudança de armazenamento foi feita.** Onde e como as credenciais reais são guardadas só muda com autorização do usuário (AGENTS.md). Varredura automática: `backend/tests/test_secrets_at_rest_scan.py` gera um token local aleatório e usa chave ElevenLabs e token do Hermes sintéticos; falha se qualquer um aparecer (UTF-8 ou UTF-16LE) ou se houver formato de credencial no diretório de dados (inclusive bancos, `-wal`, dump SQL e `.zip` de exportação), nos logs do Python, em stdout/stderr, nas respostas da API, no pacote de dados locais, na pasta restaurada ou em pastas temporárias `horus-export-*`/`horus-verify-*`. O teste usa cofre de credenciais **em memória** (`keyring.set_keyring`) e tem controle positivo que prova que o varredor detecta segredo plantado. O único local de texto puro aceito é `integrations/`.

### Em repouso

| Dado | Onde está | Controle atual (verificado) | Lacuna |
|---|---|---|---|
| Token local do backend | Gerado pelo Tauri (dois `uuid4` = 64 hex) a cada execução, passado ao backend por `HORUS_API_TOKEN` e mantido em memória pela UI (`backend_connection`). Fora do Tauri, é variável de ambiente definida pelo operador (mínimo 24 caracteres) | Nunca é gravado em arquivo, banco, log ou resposta (varredura); fora do ambiente dos workers (allowlist); o ticket de mídia é HMAC dele, não o token | Qualquer processo do mesmo usuário que leia o ambiente do backend o obtém (limitação já registrada em HOR-127). Sem rotação dentro de uma sessão |
| Chave ElevenLabs | Cofre do SO via `keyring` (Windows Credential Manager), serviço `horus.narration.elevenlabs`, conta = SHA-256 do caminho do workspace | Nunca devolvida pela API (`SecretStr`, 422 sem eco); ausente de banco, logs, exports e pacotes (varredura); falha do cofre vira `credential_store_unavailable` sem texto do erro | O Credential Manager protege por usuário do Windows, não por aplicativo. **Atenção:** `monkeypatch.setattr(keyring, 'get_keyring')` (padrão de `test_narration.py`) não isola `keyring.set_password`: os testes existentes gravam valores sintéticos no cofre real (209 entradas `horus.narration.elevenlabs` acumuladas nesta máquina, de execuções de teste). Ver plano, item 1 |
| Conexão do Hermes (URL local + token) | `integrations/hermes.json` na pasta de dados (ou `HORUS_HERMES_CONFIG`) | Arquivo ≤16 KiB; origem só `127.0.0.1`/`::1` com porta explícita; **nunca** exportado (`integrations/` excluído do pacote e do restore); sem eco em API/log; o status de segurança o rotula "arquivo local; ACLs não auditadas; sem cifragem dedicada" | **Texto puro**; ACL herdada da pasta de dados; sem rotação; não passa pelo cofre do SO |
| Configuração/perfil do Hermes real | `%LOCALAPPDATA%/hermes/profiles/horus` (fora do Hórus) | Auditoria somente leitura do perfil (`security.py`); gate de segurança bloqueia execução real | Sessões, memória e logs do Hermes real ficam fora do alcance do Hórus (já em "O que NÃO é garantido") |
| Banco operacional e Radar | `horus.sqlite3`, `radar/radar-youtube.sqlite3` (+`-wal`/`-shm`) | `secure_delete`, checkpoint `TRUNCATE`, redação por retenção; sem segredo de credencial (varredura do arquivo e do dump); roteiros, mídia e comentários são dados privados do usuário | **Sem criptografia em repouso**; depende de BitLocker; backups de migração copiam páginas livres |
| Logs | Sem arquivo de log do backend; diagnósticos de falha ficam no banco/`result.json` após `sanitized_tail` (redige `Authorization`, `Bearer`, `*key/token/secret/password`, caminhos locais; 8 KiB) | Testes: `caplog`, stdout/stderr e `sanitized_tail` sem token/chave. O Tauri não repassa tokens ao console | Logs de processos externos (Kokoro, FFmpeg, Chromium, `render-stderr.log`) dependem do mesmo saneador; um formato de segredo fora dos padrões não é redigido |
| Exports de produção | `exports/` na pasta da produção (`.zip` + manifesto) | Somente arquivos registrados e com hash; `.env`/segredo não registrado nunca entra (`test_exports.py`); varredura do `.zip` | Contém o vídeo e metadados privados: a proteção é a do diretório |
| Pacote de dados locais | `.zip` escolhido pelo usuário, fora da pasta de dados | Sem `integrations/`, `models/`, mídia; JSON com formato de credencial é excluído e listado; verificação de hash e estrutura; `contains_private_data` | O pacote **não é cifrado** e contém banco, roteiros e comentários; guarde-o como o banco |
| Pastas temporárias | `%TEMP%` (`horus-export-*`, `horus-verify-*`, `horus-narration-*`, `horus-kokoro-*`) e irmãs do destino (`.<dest>.restore-*`) | Removidas ao fim, inclusive em falha (a varredura confere as de exportação/verificação); worker com ambiente mínimo | `%TEMP%` herda a ACL do usuário e pode ser lido por antivírus/indexador; cópias do banco em `horus-export-*` existem durante a exportação |
| Preferências e estado | `narration-preference.json`, `notifications-state.json`, `production-comments/` | Sem credenciais; exportados com validação | Dados privados do usuário, sem cifragem |

### Em trânsito

| Canal | Controle atual (verificado) | Lacuna |
|---|---|---|
| UI ↔ backend | HTTP só em `127.0.0.1`; `Authorization: Bearer` obrigatório em todas as rotas (o teste percorre `app.routes`); `Host` loopback e `Origin` em lista fixa (anti DNS rebinding); CORS sem `*`; limite de corpo; comparação em tempo constante | Sem TLS na máquina (aceito: loopback); o token trafega em cabeçalho; o ticket de mídia vai na URL (`?ticket=`, validade de 1 h, HMAC) e pode aparecer em histórico do WebView |
| Backend ↔ Hermes | Somente `http://127.0.0.1\|::1:porta`, sem credenciais na URL, token em `Authorization`; gateway de saída e auditoria de transmissões na linha retida | Token em HTTP sem TLS (loopback); execução real bloqueada por gate; **isolamento de rede do SO não implementado** |
| Saídas externas | Nenhuma por padrão: sem telemetria, sem updater, `connect-src` só loopback (HOR-117); ElevenLabs exige `HORUS_NARRATION_PAID_ENABLED=1`, credencial, estimativa e autorização por texto/voz; coleta YouTube desligada; downloads só por ação explícita | Sem allowlist imposta pelo SO; o gateway único (#99) e os checkpoints humanos (#95) só entram com a linha retida; numa chamada paga futura, a chave sai do cofre para o provedor por HTTPS fora do controle do Hórus |
| Worker de produção | Ambiente mínimo por allowlist (sem token, `.env`, perfil); stdin fechado; Job Object | Sem restrição de arquivos/rede do SO (HOR-124) |

### Plano priorizado das mudanças que exigem aprovação do usuário

Nenhum item abaixo foi implementado. Ordem sugerida (risco × custo):

1. **Isolar o cofre nos testes (baixo risco, sem alterar armazenamento real).** Trocar o padrão `monkeypatch(keyring.get_keyring)` por `keyring.set_keyring` com restauração, numa fixture comum, e fazer a suíte falhar se `keyring.get_keyring()` for o cofre real durante testes. Passo seguinte, que **exige autorização do usuário**: remover as entradas sintéticas acumuladas, identificadas pelo valor de teste e nunca em massa (uma delas pode ser a credencial real do operador). Prioridade alta: é vazamento de teste no cofre real.
2. **ACL da pasta de dados.** Na criação da pasta, remover a herança e conceder acesso só ao usuário atual (e SYSTEM) em `data_dir`, `integrations/` e backups; começar com diagnóstico que apenas **avisa** quando a ACL for mais ampla. Mudar permissões é ação sensível: exige autorização e teste em pasta temporária.
3. **Mover o token do Hermes para o cofre do SO.** `hermes.json` ficaria só com a URL; o `keyring` guarda o token (como a ElevenLabs), com migração explícita e reversível, sem apagar o arquivo antigo sem confirmação. Altera onde credenciais reais são guardadas e a configuração do Hermes: **autorização obrigatória**.
4. **Rotação do token local.** O token já é por execução; faltam rotação em sessões longas (novo token ao reiniciar o backend, UI reconectando) e tirar o ticket de mídia da URL (cookie curto ou `fetch` com blob). Mudança de contrato aditiva, com snapshot.
5. **Criptografia do banco (HOR-121; maior custo).** SQLCipher (dependência nativa; empacotamento e backups mudam; a chave viria do cofre/DPAPI) ou apoiar-se no BitLocker já documentado. Depende de decisão do idealizador, benchmark e migração; não antes dos itens 1–3.
6. **Pacote de dados cifrado (opcional).** Cifrar o `.zip` com senha do usuário; esquecer a senha perde os dados, então exige decisão explícita.
7. **Isolamento de worker e rede pelo SO** (AppContainer/token restrito, HOR-124) junto ao empacotamento (HOR-100).

Verificações para este inventário: leitura do código (`config.py`, `narration/service.py`, `hermes.py`, `security.py`, `processes.py`, `local_data_export.py`, `api.py`, `main.rs`) e `test_secrets_at_rest_scan.py` (7 passed). Não executado: auditoria de ACLs do Windows, teste com o cofre real e varredura do `%TEMP%` fora das pastas do Hórus.

### Limpeza das entradas sintéticas do cofre (HOR-114) — somente após autorização do usuário

Testes antigos gravaram valores sintéticos no Credential Manager real (serviço `horus.narration.elevenlabs`); a suíte atual está isolada em memória (`backend/tests/conftest.py`). O usuário que o código de produção usa para a chave real é `sha256(str(Path(data_dir).resolve()))` (`Credentials` em `narration/service.py`), **também 64 hex**, então o formato não separa teste de produção: só se separa pela pasta de dados conhecida. `scripts/keyring-test-cleanup.py` (somente simulação por padrão) lista, sem ler valores, as entradas do serviço, protege as que coincidem com a pasta padrão do Hórus, a do Tauri (`%LOCALAPPDATA%/com.horus.audiovisual/workspace`), `HORUS_DATA_DIR` e cada `--protect-dir`, e imprime contagens e usuários truncados. `--apply` só apaga com a confirmação digitada `APAGAR <n>` e nunca toca as protegidas. **A exclusão depende de autorização do usuário e não foi executada**: pastas de dados reais fora das conhecidas (worktrees de desenvolvimento) precisam ser informadas por `--protect-dir`, ou serão tratadas como candidatas. Testes em `backend/tests/test_keyring_cleanup_script.py` (cofre em memória).

### Verificação do estado da criptografia de disco (HOR-119)

`GET /v1/diagnostics` ganhou a chave de topo `disk_encryption` (aditiva; fora de `production.checks`, `missing` e `ready`, então **nunca bloqueia produção**): `{status: encrypted|not_encrypted|unverified, protection: on|off|unknown, method: null, volume: "C:", detail, guidance}`. Implementação em `backend/src/horus_backend/disk_encryption.py`: somente leitura, **sem administrador**, lendo a propriedade de shell `System.Volume.BitLockerProtection` do volume da pasta de dados (a mesma do cadeado do Explorador) por um comando PowerShell fixo, via `processes.run_sync` (ambiente mínimo, prazo de 8 s, resultado em cache por 5 min). `Get-BitLockerVolume`, `manage-bde` e o WMI `Win32_EncryptableVolume` exigem elevação e não são usados; nada é ativado, suspenso ou alterado no sistema. A resposta traz só a letra do volume.

Interpretação: valor `1` = `encrypted`/`on`; `2` = `not_encrypted`/`off` (volume sem criptografia **ou com a proteção suspensa**: a propriedade não diferencia); qualquer outro valor, erro, prazo esgotado, volume sem letra (UNC) ou plataforma sem a consulta = `unverified` com orientação. A propriedade não distingue BitLocker de criptografia de dispositivo, por isso `method` é `null`. O texto `guidance` (pt-BR) explica o que é, por que importa para os dados do Hórus, como ativar (Pro/Enterprise/Education: Criptografia de dispositivo ou BitLocker no Painel de Controle; **Home não tem BitLocker**: usar Criptografia de dispositivo, em computadores compatíveis, com conta Microsoft) e lembra de guardar a chave de recuperação fora do computador.

Verificado: o mapeamento `1`/`2` e o texto estão cobertos por testes com consulta simulada (`test_disk_encryption.py`); na máquina de desenvolvimento (Windows 10 Pro, sem BitLocker ativo) a consulta real devolveu `2`. **Não foi possível confirmar o valor `1` em um volume realmente criptografado** (sem volume assim disponível); até essa confirmação, o resultado `encrypted` deve ser tratado como a leitura documentada pela comunidade, não como certificação. A exibição do aviso na interface não faz parte deste incremento.

## Endurecimento de credenciais e da pasta de dados (HOR-120): implementado em ferramentas; aplicação real depende de autorização

Nada nesta seção foi aplicado a dados ou credenciais reais. Cada item tem ferramenta testada em ambiente isolado (cofre em memória, pasta e `HORUS_DATA_DIR` temporários) e a aplicação real fica **dependente de autorização do usuário**, com os comandos exatos abaixo.

### 7.2 Token do Hermes no cofre do SO

`hermes.read_connection` agora **prefere o token do cofre** (`keyring`, serviço `horus.hermes.connection`, usuário = SHA-256 do caminho resolvido do arquivo de conexão) e usa o token do arquivo atual (`integrations/hermes.json`) quando o cofre está indisponível, vazio ou guarda valor inválido. A URL continua vindo do arquivo; o campo `token` do arquivo passa a ser opcional quando o cofre o tem. Sem o cofre, o comportamento é o anterior. Módulo: `backend/src/horus_backend/hermes_credential.py`; testes: `backend/tests/test_hermes_credential.py`.

Migração (simulação por padrão; copia, **relê e compara**; não apaga o arquivo e não revoga nem rotaciona nada; nunca imprime o valor):

```powershell
# 1) simulação (não altera nada)
& backend/.venv/Scripts/python.exe -m horus_backend.hermes_credential migrate --config "<pasta-de-dados>\integrations\hermes.json"
# 2) APLICAR NO COFRE REAL: somente com autorização do usuário; pede a confirmação digitada MIGRAR
& backend/.venv/Scripts/python.exe -m horus_backend.hermes_credential migrate --config "<pasta-de-dados>\integrations\hermes.json" --apply
```

Um token diferente já presente no cofre nunca é sobrescrito sem `--replace`. **Remover o campo `token` do arquivo** é um passo manual posterior e também exige autorização (reversão: gravar o token de volta no arquivo ou apagar a entrada do cofre; o arquivo original só deve ser alterado depois de a leitura pelo cofre ter sido conferida com a verificação do Hermes em Ajustes). A tela de segurança (`/v1/security/status`) continua descrevendo o arquivo como local do token até esse passo final.
