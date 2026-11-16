-- Prova isolada: duas contas fictícias; nenhuma credencial ou dado real.
-- Executar em conexão administrativa, nunca no caminho do usuário; ROLLBACK obrigatório.
BEGIN;
DO $$
DECLARE role_name text; t record;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['horus_sync_service','horus_sync_login'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname=role_name AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication) THEN
      RAISE EXCEPTION 'unsafe service/login role'; END IF;
    IF EXISTS (SELECT FROM pg_roles p WHERE p.rolname IN ('postgres','service_role','supabase_admin','authenticated')
        AND pg_has_role(role_name,p.oid,'MEMBER')) THEN RAISE EXCEPTION 'privileged membership'; END IF;
  END LOOP;
  FOR t IN SELECT c.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='horus_sync' AND c.relkind='r' LOOP
    IF NOT t.relrowsecurity OR NOT t.relforcerowsecurity THEN RAISE EXCEPTION 'RLS missing'; END IF;
    IF pg_get_userbyid(t.relowner) IN ('horus_sync_service','horus_sync_login') THEN
      RAISE EXCEPTION 'service owns table'; END IF;
    IF has_table_privilege('anon',t.oid,'SELECT,INSERT,UPDATE,DELETE')
       OR has_table_privilege('authenticated',t.oid,'SELECT,INSERT,UPDATE,DELETE') THEN
      RAISE EXCEPTION 'table exposed'; END IF;
  END LOOP;
  IF has_schema_privilege('anon','horus_sync','USAGE') OR has_schema_privilege('authenticated','horus_sync','USAGE') THEN
    RAISE EXCEPTION 'schema exposed'; END IF;
  IF EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='horus_sync' AND p.prosecdef) THEN RAISE EXCEPTION 'definer function'; END IF;
  IF EXISTS (SELECT FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace,
      LATERAL aclexplode(d.defaclacl) a WHERE n.nspname='horus_sync'
      AND a.grantee IN (0, (SELECT oid FROM pg_roles WHERE rolname='anon'),
                          (SELECT oid FROM pg_roles WHERE rolname='authenticated'))) THEN
    RAISE EXCEPTION 'unsafe defaults'; END IF;
END $$;
-- Delegação temporária ao executor administrativo, revertida no ROLLBACK.
DO $$ BEGIN EXECUTE format('GRANT horus_sync_login TO %I',current_user); END $$;
INSERT INTO auth.users(id,email) VALUES
 ('139b0000-0000-4000-8000-000000000001','hor139b-a@synthetic.invalid'),
 ('139b0000-0000-4000-8000-000000000002','hor139b-b@synthetic.invalid');
SET LOCAL ROLE horus_sync_login;
SELECT set_config('request.jwt.claims','{"sub":"139b0000-0000-4000-8000-000000000001","aud":"authenticated","role":"authenticated"}',true);
INSERT INTO horus_sync.identities(owner_id) VALUES('139b0000-0000-4000-8000-000000000001');
INSERT INTO horus_sync.workspaces(owner_id,id,label) VALUES
 ('139b0000-0000-4000-8000-000000000001','139b0000-0000-4000-8000-000000000011','HOR139B synthetic A');
SELECT horus_sync.submit_revision('139b0000-0000-4000-8000-000000000021',
 '139b0000-0000-4000-8000-000000000011','139b0000-0000-4000-8000-000000000031',0,'workflow','{"text":"HOR139B synthetic A"}');
-- Retry idêntico não cria nova versão nem avança cursor.
SELECT horus_sync.submit_revision('139b0000-0000-4000-8000-000000000021',
 '139b0000-0000-4000-8000-000000000011','139b0000-0000-4000-8000-000000000031',0,'workflow','{"text":"HOR139B synthetic A"}');
DO $$ BEGIN
  IF (SELECT count(*) FROM horus_sync.revisions) <> 1 THEN RAISE EXCEPTION 'idempotency failed'; END IF;
  IF (SELECT cursor FROM horus_sync.workspaces) <> 1 THEN RAISE EXCEPTION 'cursor duplicated'; END IF;
  BEGIN
    PERFORM horus_sync.submit_revision('139b0000-0000-4000-8000-000000000021',
     '139b0000-0000-4000-8000-000000000011','139b0000-0000-4000-8000-000000000031',0,'workflow','{"text":"changed"}');
    RAISE EXCEPTION 'divergent replay accepted'; EXCEPTION WHEN serialization_failure THEN NULL;
  END;
  BEGIN
    PERFORM horus_sync.submit_revision('139b0000-0000-4000-8000-000000000022',
     '139b0000-0000-4000-8000-000000000011','139b0000-0000-4000-8000-000000000031',0,'workflow','{}');
    RAISE EXCEPTION 'stale base accepted'; EXCEPTION WHEN serialization_failure THEN NULL;
  END;
END $$;
SELECT set_config('request.jwt.claims','{"sub":"139b0000-0000-4000-8000-000000000002","aud":"authenticated","role":"authenticated"}',true);
INSERT INTO horus_sync.identities(owner_id) VALUES('139b0000-0000-4000-8000-000000000002');
INSERT INTO horus_sync.workspaces(owner_id,id,label) VALUES
 ('139b0000-0000-4000-8000-000000000002','139b0000-0000-4000-8000-000000000012','HOR139B synthetic B');
SELECT horus_sync.submit_revision('139b0000-0000-4000-8000-000000000023',
 '139b0000-0000-4000-8000-000000000012','139b0000-0000-4000-8000-000000000032',0,'script','{"text":"HOR139B synthetic B"}');
DO $$ BEGIN
  IF (SELECT count(*) FROM horus_sync.revisions) <> 1 THEN RAISE EXCEPTION 'cross-account count'; END IF;
  IF EXISTS (SELECT FROM horus_sync.revisions WHERE entity_id='139b0000-0000-4000-8000-000000000031') THEN
    RAISE EXCEPTION 'cross-account read'; END IF;
  UPDATE horus_sync.workspaces SET cursor=999 WHERE owner_id='139b0000-0000-4000-8000-000000000001';
  IF FOUND THEN RAISE EXCEPTION 'cross-account update'; END IF;
  BEGIN
    INSERT INTO horus_sync.identities(owner_id) VALUES('139b0000-0000-4000-8000-000000000001');
    RAISE EXCEPTION 'forged owner accepted'; EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    UPDATE horus_sync.workspaces SET owner_id='139b0000-0000-4000-8000-000000000001';
    RAISE EXCEPTION 'owner reassignment accepted'; EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    DELETE FROM horus_sync.revisions;
    RAISE EXCEPTION 'append-only deletion accepted'; EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    UPDATE horus_sync.revisions SET payload='{}';
    RAISE EXCEPTION 'append-only update accepted'; EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    INSERT INTO horus_sync.revisions(owner_id,workspace_id,entity_id,server_version,base_version,cursor,kind,payload)
      VALUES('139b0000-0000-4000-8000-000000000002','139b0000-0000-4000-8000-000000000011',
             '139b0000-0000-4000-8000-000000000033',1,0,2,'workflow','{}');
    RAISE EXCEPTION 'cross-workspace FK accepted'; EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
END $$;
-- A também não enxerga B, nem pode chamar CAS no workspace B.
SELECT set_config('request.jwt.claims','{"sub":"139b0000-0000-4000-8000-000000000001","aud":"authenticated","role":"authenticated"}',true);
DO $$ BEGIN
  IF (SELECT count(*) FROM horus_sync.revisions) <> 1 THEN RAISE EXCEPTION 'reverse cross-account count'; END IF;
  BEGIN
    PERFORM horus_sync.submit_revision('139b0000-0000-4000-8000-000000000024',
      '139b0000-0000-4000-8000-000000000012','139b0000-0000-4000-8000-000000000032',1,'script','{}');
    RAISE EXCEPTION 'cross-account CAS accepted'; EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  IF EXISTS (SELECT FROM horus_sync.revisions WHERE content_hash <> encode(sha256(convert_to(payload::text,'UTF8')),'hex')) THEN
    RAISE EXCEPTION 'server hash mismatch'; END IF;
END $$;
SELECT set_config('request.jwt.claims','{}',true);
DO $$ BEGIN
  IF EXISTS (SELECT FROM horus_sync.identities) THEN RAISE EXCEPTION 'missing claims not denied'; END IF;
END $$;
SELECT set_config('request.jwt.claims','{"sub":"139b0000-0000-4000-8000-000000000001","aud":"wrong","role":"authenticated"}',true);
DO $$ BEGIN
  IF EXISTS (SELECT FROM horus_sync.identities) THEN RAISE EXCEPTION 'wrong audience accepted'; END IF;
END $$;
SELECT set_config('request.jwt.claims','{"aud":"authenticated","role":"authenticated"}',true);
DO $$ BEGIN
  IF EXISTS (SELECT FROM horus_sync.identities) THEN RAISE EXCEPTION 'missing sub accepted'; END IF;
END $$;
SELECT set_config('request.jwt.claims','{"sub":"invalid-uuid","aud":"authenticated","role":"authenticated"}',true);
DO $$ BEGIN
  BEGIN
    PERFORM horus_sync.current_owner();
    RAISE EXCEPTION 'invalid UUID accepted'; EXCEPTION WHEN invalid_text_representation THEN NULL;
  END;
END $$;
RESET ROLE;
ROLLBACK;
-- Mesmo backend físico, nova transação sem claims: nada pode vazar do request anterior.
BEGIN;
DO $$ BEGIN EXECUTE format('GRANT horus_sync_login TO %I',current_user); END $$;
SET LOCAL ROLE horus_sync_login;
DO $$ BEGIN
  IF nullif(current_setting('request.jwt.claims',true),'') IS NOT NULL THEN RAISE EXCEPTION 'transaction claims leaked'; END IF;
END $$;
RESET ROLE;
ROLLBACK;
-- Também após COMMIT, sem fixture nem concessão administrativa persistida.
BEGIN;
SELECT set_config('request.jwt.claims','{"sub":"139b0000-0000-4000-8000-000000000001","aud":"authenticated","role":"authenticated"}',true);
COMMIT;
BEGIN;
DO $$ BEGIN
  IF nullif(current_setting('request.jwt.claims',true),'') IS NOT NULL THEN RAISE EXCEPTION 'committed claims leaked'; END IF;
END $$;
ROLLBACK;
