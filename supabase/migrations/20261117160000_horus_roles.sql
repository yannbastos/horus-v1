-- HOR-164: papéis sem credencial; provisioning de LOGIN é externo ao aplicativo.
DO $$
DECLARE r text;
BEGIN
 FOREACH r IN ARRAY ARRAY['horus_owner','horus_app'] LOOP
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',r);
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname=r AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
   RAISE EXCEPTION 'Papel incompatível com least privilege';
  END IF;
 END LOOP;
 IF pg_has_role('horus_app','horus_owner','MEMBER') THEN
  RAISE EXCEPTION 'Aplicação não pode herdar o owner';
 END IF;
 IF EXISTS (SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
            WHERE r.rolname='horus_app') THEN
  RAISE EXCEPTION 'Aplicação não pode herdar papéis adicionais';
 END IF;
 EXECUTE format('REVOKE CREATE, TEMPORARY ON DATABASE %I FROM PUBLIC, anon, authenticated, horus_app', current_database());
END $$;
CREATE SCHEMA horus AUTHORIZATION horus_owner;
REVOKE ALL ON SCHEMA horus FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SET ROLE horus_owner;
ALTER DEFAULT PRIVILEGES IN SCHEMA horus REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA horus REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA horus REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;
CREATE TABLE horus.schema_migrations(version text PRIMARY KEY, checksum text NOT NULL CHECK(checksum ~ '^[0-9a-f]{64}$'), applied_at timestamptz NOT NULL, app_min text, app_max text);
CREATE TABLE horus.database_identity(singleton boolean PRIMARY KEY CHECK(singleton), instance_id uuid NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO horus.database_identity VALUES(true,gen_random_uuid(),clock_timestamp());
RESET ROLE;
