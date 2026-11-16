# Fase B — persistência opcional no horus-dev (HOR-139)

**15/11/2026.** Contrato aprovado com ajustes na [revisão de segurança da #126](https://github.com/yannbastos/horus-management/issues/126#issuecomment-6894844016). Complementa o [ADR aceito](adr-persistencia-local-remota.md), o [inventário](inventario-persistencia.md) e [R1–R13](seguranca-persistencia-remota.md). Não integra Hermes retido, não remove SQLite e não muda o primeiro vídeo.

## Decisão de hospedagem

Escolhidas **Edge Functions do próprio horus-dev**, na organização/projeto já fornecidos pelo usuário, para o futuro serviço B. Evita outro provedor/projeto e mantém Auth e banco na mesma plataforma. Data API continua desativada; o serviço usará driver Postgres com pooler transacional, sem `supabase-js` para tabelas. Hospedagem é decisão desta fase; **não há função HTTP implementada ou implantada**, senha provisionada, sessão remota no desktop ou cliente de sincronização ativo.

Condições públicas consultadas em 15/11/2026: Free inclui **500.000 invocações/mês**, sem excedente Free indicado na tabela; a cobrança de US$ 2/milhão é descrita para excedentes dos planos pagos. Não habilitar cobrança, add-ons nem upgrade. [Pricing de Functions](https://supabase.com/docs/guides/functions/pricing). Limites: memória **256 MB**, duração Free **150 s**, CPU **2 s por pedido**, idle timeout **150 s**, até **100 funções**; bundle de 20 MB local ou 5 MB via servidor. [Limites oficiais](https://supabase.com/docs/guides/functions/limits). Esses limites compartilhados são teto, não SLA nem reserva exclusiva. Pausa/indisponibilidade suspende apenas sync; o vídeo local permanece independente. O ensaio desta fase não faz invocações de Functions.

A documentação recomenda pooler em modo transação para Edge/serverless e preparação de statements compatível com esse modo. [Conexões Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres) e [drivers serverless](https://supabase.com/docs/guides/database/connecting-to-postgres/serverless-drivers). A [documentação de segurança](https://supabase.com/docs/guides/api/securing-your-api#disable-the-data-api) confirma que desativar Data API impede endpoints auto-gerados independentemente dos grants; não foi alterada essa configuração remota.

## Contrato do serviço futuro — bloqueia deploy até ser implementado e testado

- JWT individual verificado criptograficamente por biblioteca JOSE/JWKS mantida: chave do issuer esperado, assinatura válida, algoritmo exclusivamente ES256/RS256, issuer do horus-dev, `aud=authenticated`, `role=authenticated`, expiração e `sub` UUID. Falhar fechado se as chaves atuais do projeto não forem compatíveis. Não confiar em claims decodificadas, usuário informado pelo desktop ou chave privilegiada.
- `validate_verified_identity` é somente validação semântica **depois** da verificação criptográfica; não é um autenticador e não possui rota. Testes cobrem sub/aud/alg/exp/issuer/role, sem prometer verificação de assinatura hoje.
- Nunca ler ou usar `SUPABASE_SERVICE_ROLE_KEY`, credencial administrativa ou login `postgres` no serviço. Conectar exclusivamente por `horus_sync_login`, herdeiro de `horus_sync_service`, sem rolsuper/BYPASSRLS/CREATEROLE/CREATEDB/replication, membership privilegiada ou ownership. O login foi criado sem senha pela migração e não pode autenticar por senha até provisionamento seguro.
- URL de banco exclusivamente em secret do ambiente remoto confiável (nome próprio `HORUS_SYNC_DATABASE_URL`); não no desktop/FastAPI/arquivos versionados, logs ou respostas. TLS com certificado e hostname verificados; não aceitar opções de conexão que desativem a verificação. Não imprimir secrets ao diagnosticar erro. Provisionamento/rotação permanecem fora desta entrega.
- Abrir transação, definir somente claims mínimas verificadas por `set_config('request.jwt.claims', ..., true)`, executar SQL parametrizado e encerrar com commit/rollback. Nada de `SET` de sessão. O helper RLS lê exclusivamente esse envelope, sem fallback para o GUC legado `request.jwt.claim.sub`. Não manter transação aberta durante chamadas externas.
- Limite de pedido **64 KiB**, até **100 mudanças** e **32 KiB por payload**; o menor limite prevalece. Rate limit proposto **60 pedidos/minuto por usuário**, com contador durável/atômico no servidor e teto global do ensaio; rejeitar com 429 antes de trabalho custoso. Não confiar em contador de uma única instância Edge. Implementação do rate limit é porta obrigatória antes do deploy; nenhum serviço/add-on pago é autorizado.
- Erros 400/401/403/409/429/503 sem SQL, DSN, tokens ou texto editorial. `40001` e unicidade divergente viram conflito explícito 409. Reenvio idêntico retorna o mesmo recibo; não transforma recibo em execução. Cursor opaco deve ser vinculado à conta/workspace, sem supor sequence global como ordem de commit.

## Schema aplicado e autorização

`supabase/config.toml` versionado sem segredos, API local desativada e seed desabilitado. Projeto vinculado por `npx supabase link --project-ref uzirimqmwohnfraszvjn`. Na CLI **3.1.0**, `db push` exige `--remote` explícito: a chamada sem essa flag mira ambiente local. O rótulo da CLI **production** designa a branch primária do projeto Supabase; o nome/ref confirmado foi exclusivamente **horus-dev**, desenvolvimento autorizado, não a produção do aplicativo.

Migração `20261115150000_sync_metadata.sql` aplicada com `npx supabase db push --remote=uzirimqmwohnfraszvjn --skip-vault --yes`, após validação transacional com rollback e dry-run. Não houve pedido de senha e não foram lidos tokens/strings de conexão. `.temp/`, `.branches/` e arquivos `.env*` ficam ignorados. Não usar debug nem argumentos de senha. Se outra máquina pedir senha, parar e executar no terminal do usuário `npx supabase link --project-ref uzirimqmwohnfraszvjn` e `npx supabase db push --remote=uzirimqmwohnfraszvjn --skip-vault` sem compartilhar o valor.

Schema privado `horus_sync`:

| Objeto | Garantia |
|---|---|
| `identities` | UUID individual referencia `auth.users`; sem cópia de email/sessão/credencial |
| `workspaces` | proprietário único na Fase B, PK composta com owner, label limitada e cursor de servidor |
| `revisions` | PK `(owner_id,entity_id,server_version)`, FK composta de workspace; kind/payload/base/version/tombstone limitados; histórico append-only |
| `receipts` | PK `(owner_id,operation_id)`, hash canônico e referência de revisão; recibo de outbox enviado, não fila executora |
| `current_owner()` | somente claims transacionais com aud/role corretos; UUID inválido falha fechado; sem SECURITY DEFINER |
| `submit_revision()` | SECURITY INVOKER/search_path fixo; lock do workspace até commit serializa CAS/feed; hash de request recalculado do JSONB no servidor |
| trigger `revision_hash` | recalcula SHA-256 do payload no servidor até em INSERT direto; não confia no hash do cliente |

RLS ENABLE/FORCE em todas as quatro tabelas; SELECT/INSERT por owner com WITH CHECK, UPDATE apenas do cursor do workspace próprio. Serviço sem UPDATE/DELETE em revisões/recibos nem permissão para alterar owner; anon/authenticated/PUBLIC sem USAGE no schema ou grants de dados/funções. Defaults do schema não expõem tabelas/sequências; funções novas exigem revoke PUBLIC explícito, pois default global de funções não pode ser removido só por schema. Serviço é componente confiável que estabelece a identidade: RLS não autentica JWT nem impede o próprio serviço comprometido de forjar claims.

Retenção/apagamento cabe somente ao proprietário administrativo do schema, fora do caminho do usuário e após revisão de ack/snapshot/retorno. Fase B deixa **zero registros** após o ensaio. Não há rotina automática de limpeza remota ou autorização de apagar dados operacionais. Em C/D será necessário fixar a janela dos recibos/tombstones e tratar dispositivos expirados antes de qualquer purge; excluir tombstone cedo pode ressuscitar conteúdo. Sem compartilhamento/membership entre usuários nesta fase.

## Portas locais implementadas — desligadas por padrão

`persistence_contracts.py` define `JobRepository` e adapter `LocalJobRepository` que delega ao Store existente, DTO `Change`, portas `SyncJournal`/`RemoteMetadataGateway`, `LocalUnitOfWork` e `LocalSyncJournal`. Nenhum consumidor do aplicativo foi trocado. Repositórios editoriais/de produção específicos seguem nos serviços atuais; sua extração e a integração de outbox com cada transição ficam para a transferência/sync, preservando atomicidade e contratos existentes.

`LocalUnitOfWork(..., enabled=False)` recusa abertura sem criar tabelas. Opt-in explícito cria apenas três tabelas auxiliares no mesmo SQLite escolhido pelo chamador; testes usam bases temporárias. Outbox e escrita de domínio podem usar a **mesma conexão/transação**, sem HTTP dentro dela. DTO só aceita texto/título e histórico marcado, sem configuração arbitrária; filtros conservadores recusam padrões de sessão/credencial, não substituem uma política completa de seleção/redação de conteúdo.

Outbox idempotente segregada por owner/workspace, no máximo **1.000 entradas/4 MiB**; payload até 32 KiB e batches até 100. Inbox também limitada, com cursor e conteúdo atômicos. Ack divergente e cursor regressivo são conflitos; inbox não chama jobs/aprovações. `logout(owner)` apaga somente cópias de transporte dessa conta (outbox/inbox/cursor), preservando originais autorais e outras contas. Uma futura UI precisa informar pendências e permitir re-selecionar/exportar antes de purgar o transporte; a fila não pode ser a única cópia de trabalho local. Cofre/sessão e UX de logout ainda não existem nesta implementação.

## Evidências e limites

`supabase/tests/isolation.sql` executado no **PostgreSQL 17.11 do horus-dev**: duas linhas de conta Auth sintéticas com prefixo `139b`/emails `synthetic.invalid`; assume o papel restrito, testa SELECT/count, UPDATE/DELETE, owner forjado, FK entre workspaces, CAS obsoleto, replay divergente, hash de servidor, claims ausentes/aud incorreto e reset transacional após rollback e commit. Teste de catálogo cobre ambos os papéis, membership/ownership, RLS/FORCE, grants/defaults e ausência de SECURITY DEFINER. A concessão temporária que permite ao executor administrativo assumir o papel também é revertida por rollback.

Prova é de **SQL/RLS com identidade sintética injetada**, não de login Auth/JWT HTTP nem de conexão real do futuro login via pooler. Reutilização da conexão foi ensaiada por transações sequenciais via CLI administrativa; compatibilidade operacional do driver/pooler e rotação/expiração de sessão ainda dependem do serviço futuro. Não afirmar R5/R7/R8 completos: TLS negativo do futuro driver, cofre/logout da sessão e dump/restauração pertencem às fases posteriores. R11/R12 não foram modificados.

Após o ensaio, `audit.sql` confirmou **0 contas sintéticas remanescentes e 0 linhas nas quatro tabelas**, tabelas pertencentes a `postgres` com RLS/FORCE e ambos os papéis sem rolsuper/rolbypassrls/createrole/createdb/replication; único membership de login é o papel restrito. Não se consultou conteúdo operacional.

Testes locais verificam opt-in desligado, delegação ao Store, atomicidade/rollback outbox+escrita, segregação, replay/ack, quotas, conteúdo proibido, inbox/cursor, logout e semântica de claims. Snapshot da API local e regressões do primeiro vídeo permanecem inalterados. Nenhuma Function implantada, serviço pago ativado, mídia enviada ou dado real migrado.
