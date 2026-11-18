# Validação do Supabase de desenvolvimento

Use um projeto novo e dedicado ao desenvolvimento, com dados sintéticos. No painel do Supabase, escolha a região e o plano. O plano Free pode pausar por inatividade e não inclui backups automáticos recuperáveis pelo painel. Revise os limites, os preços e as condições de backup antes de decidir entre Free e um plano pago. [Planos do Supabase](https://supabase.com/pricing)

1. Crie o projeto dev e guarde a senha administrativa do banco. Na raiz do repositório, autentique a CLI com `npx supabase login`. No painel **Connect**, copie o host do **Session pooler** e a referência de 20 caracteres do projeto. A autenticação da CLI é independente da sessão PostgreSQL usada pelo kit.
2. No SQL Editor do projeto dev, prepare o papel dono para que a sessão administrativa `postgres` possa executar `SET ROLE horus_owner`. Em um projeto novo, execute:

   ```sql
   CREATE ROLE horus_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
   GRANT horus_owner TO postgres;
   ```

   A sessão administrativa precisa de `CREATEROLE`, `BYPASSRLS` e privilégios para criar schemas e aplicar os grants e revokes da cadeia SQL. O aplicador usa a porta 5432 e a CA embarcada com `verify-full`. Use um terminal interativo, com o ambiente Python do backend já preparado:

   ```powershell
   $env:PYTHONPATH = (Resolve-Path backend/src).Path
   & backend/.venv/Scripts/python.exe scripts/pg_apply_migrations.py
   & backend/.venv/Scripts/python.exe scripts/pg_apply_migrations.py --apply
   ```

   O primeiro comando consulta o histórico e lista as versões pendentes. Só rode o segundo depois de confirmar o destino dev. O aplicador pede a senha separadamente, sem eco, e aplica toda a cadeia sob uma transação e o lock exclusivo de migração. O ledger registra SHA-256 do SQL normalizado em UTF-8/LF sem BOM, inclusive a migration histórica de `horus_sync`. Em repetição, valida os registros existentes. Use apenas esse aplicador para a cadeia deste projeto. Um schema já criado por outro mecanismo, sem o histórico correspondente, exige revisão. Se houver erro durante o commit, rode novamente o dry-run para conferir o ledger antes de reaplicar.
3. No SQL Editor, habilite o login com `ALTER ROLE horus_app LOGIN;`. Defina a senha do papel pelo prompt interativo `\password horus_app` do `psql`, em sessão administrativa com TLS verificado. Use uma senha exclusiva para esse papel e mantenha a senha administrativa fora do app. O usuário do pooler é `horus_app.<referência-do-projeto>`.
4. Abra **Ajustes → Banco de dados** no Hórus. Informe o host do **Session pooler**, porta `5432`, banco `postgres`, usuário do passo anterior e senha de `horus_app`. Salve no cofre via interface. Execute o comando abaixo com a mesma conta de sistema e o mesmo diretório de dados que o app usa. Se o app usa `HORUS_DATA_DIR`, preserve o mesmo valor no terminal.

   ```powershell
   & backend/.venv/Scripts/python.exe scripts/validate_supabase_dev.py
   ```

O relatório JSON em stdout traz o status, a etapa de falha, as etapas aprovadas e a versão esperada. A transação de teste altera apenas um parâmetro local à transação, verifica a reversão e não grava linhas. O teste de lease verifica os locks e o acesso às colunas de coordenação em modo somente leitura, sem registrar um worker.

| Saída | Etapa |
| --- | --- |
| 0 | Todas as verificações passaram |
| 10 | Cofre indisponível ou configuração ausente |
| 11 | Endpoint recusado |
| 12 | Resolução DNS |
| 13 | TLS |
| 14 | Conexão |
| 15 | Autenticação |
| 16 | Papel da aplicação |
| 17 | Ledger ou checksum |
| 18 | RLS |
| 19 | Transação e rollback |
| 20 | Pool de conexões |
| 21 | Locks ou contrato de lease |

O ensaio automatizado usa o PostgreSQL descartável e simula DNS e TLS. O projeto dev criado por você é o ambiente para confirmar o certificado, o papel administrativo e o Supavisor reais.
