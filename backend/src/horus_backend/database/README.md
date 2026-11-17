# Camada PostgreSQL — HOR-164 I1

Contrato HTTP: `GET /v1/database/status`, `POST/DELETE /v1/database/connection`
e `POST /v1/database/test`. Os diagnósticos retornam HTTP 200 e oito estados;
recusas usam `detail: {code, message, status}`. A senha é somente escrita.
O POST salva descritor e senha juntos no cofre nativo, relê e verifica antes
de conectar. Não há arquivo, variável de ambiente ou banco alternativo.
GET consulta apenas snapshot imutável, sem rede/cofre e sem alterar checked_at.
POST test concorrente é deduplicado e responde 200 com o snapshot connecting;
o bloqueio por troca em andamento aplica-se somente a POST/DELETE connection.
Reserva de atualização continua bloqueando mutações, sem bloquear o diagnóstico.
Falha do cofre sempre remove host_label do snapshot. Corpos chunked acima de
16 KiB recebem 422 sem invalidar o pool ou alterar o diagnóstico existente.

O pool síncrono tem 1–4 conexões e mais uma conexão dedicada de coordenação.
Troca/exclusão fecham admissão, aguardam até 3 s pelas UoWs e invalidam a
geração anterior. Checkout: 3 s; conexão: 5 s; SQL: 15 s; lock: 2 s;
idle em transação: 15 s. Verificações de leitura têm até três tentativas,
com esperas de 1/2 s mais jitter de até 250 ms, somente em indisponibilidade.
Não há repetição automática de transações de domínio ou COMMIT.
O pool usa check no checkout, idle excedente 60 s, lifetime 900 s com jitter
do psycopg_pool e keepalive 30/10/3. Operações SQL devem usar uma conexão
por UoW e transação explícita; o pool usa autocommit fora dessas transações.

O app segura advisory lock compartilhado `(1213157973, 164)` na conexão
dedicada. O migrador separado deve usar o exclusivo correspondente; o app
não executa DDL. Este lock coordena manutenção, não substitui claim/lease
e fencing de executores, que pertencem aos incrementos de domínio.

Por decisão do coordenador, antes do provisionamento, a allowlist admite
um único label DNS canônico sob `.pooler.supabase.com`, somente TCP 5432.
O hostname real do projeto de desenvolvimento ainda deve ser confirmado.
Direto `db.<ref>.supabase.co`, IP literal, host privado e opções arbitrárias
são recusados. Cada conexão resolve novamente A/AAAA, valida **todos** os
endereços e usa hostaddr validado mantendo host DNS para verify-full/SNI.
Opções de ambiente `PG*` são recusadas; não há configuração libpq concorrente.
DNS temporariamente indisponível permite salvar a configuração offline;
resposta DNS privada recusa o POST antes da alteração.

## CA pública embarcada

`supabase-ca.crt` é a **Supabase Root 2021 CA**, válida de 28/04/2021 a
26/04/2031, baixada em 17/11/2026 por HTTPS verificado da distribuição
[Supabase](https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt).
A [documentação oficial](https://supabase.com/docs/guides/platform/ssl-enforcement)
determina CA Supabase e verify-full. O endereço antigo
`https://supabase.com/prod-ca-2021.crt` retornou 404 nesta data.

- SHA-256 do arquivo: `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.
- Fingerprint SHA-256 do certificado DER: `807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa`.

É certificado público, sem chave privada. Nenhuma CA corporativa é usada
para PostgreSQL. Rotação exige revisão de fonte/fingerprint e novo pacote;
falha TLS não autoriza require, desativação de validação ou trust global.

## Limites do incremento

O heartbeat da conexão dedicada roda a cada 10 s e fecha admissão/geração
ao detectar perda. O primeiro erro de autenticação de uma conexão nova
invalida a geração e desperta o monitor para fechar o pool; não há retry
infinito de senha. Esse mecanismo não substitui os tokens de fencing de
domínio dos incrementos seguintes.

Os módulos legados do produto ainda usam SQLite, por escopo explícito.
Este pacote não importa SQLite, cria banco local, migra dados nem chama
Supabase em testes. A integração exclusiva PG e a remoção do legado ficam
nos incrementos seguintes. Dependências psycopg/psycopg_pool estão no extra
test do lock atual: o integrador deve promovê-las ao runtime/empacotamento
antes do corte. Sem conta Supabase provisionada, TLS positivo/Supavisor e
grants reais continuam pendentes de prova sintética remota autorizada.
