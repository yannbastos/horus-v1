-- HOR-164: funções invoker; grants e RLS são barreiras independentes.
SET ROLE horus_owner;
-- Lock de statement antecede inclusive a atribuição de identity de cada linha.
-- Namespace 164; chaves 1/2/3 reservadas aos feeds globais abaixo.
CREATE FUNCTION horus.append_lock() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(164, CASE TG_TABLE_NAME
  WHEN 'transmissions' THEN 1 WHEN 'human_checkpoints' THEN 2
  WHEN 'deletion_events' THEN 3 END);
 RETURN NULL;
END $$;
CREATE TRIGGER transmissions_append_lock BEFORE INSERT ON horus.transmissions
 FOR EACH STATEMENT EXECUTE FUNCTION horus.append_lock();
CREATE TRIGGER human_checkpoints_append_lock BEFORE INSERT ON horus.human_checkpoints
 FOR EACH STATEMENT EXECUTE FUNCTION horus.append_lock();
CREATE FUNCTION horus.no_update() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN RAISE EXCEPTION 'append-only: UPDATE recusado' USING ERRCODE='23514'; END $$;
CREATE FUNCTION horus.no_delete() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN RAISE EXCEPTION 'append-only: DELETE recusado' USING ERRCODE='23514'; END $$;
CREATE FUNCTION horus.consume_once() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
 IF OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
  OR NEW.consumed_ref IS NULL
  OR NEW.consumed_at < OLD.approved_at OR NEW.consumed_at >= OLD.expires_at
  OR clock_timestamp() >= OLD.expires_at
  OR (to_jsonb(NEW) - ARRAY['consumed_at','consumed_ref'])
       IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['consumed_at','consumed_ref']) THEN
  RAISE EXCEPTION 'checkpoint: consumo único válido exigido' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER human_checkpoints_consume_once BEFORE UPDATE ON horus.human_checkpoints
 FOR EACH ROW EXECUTE FUNCTION horus.consume_once();
CREATE TRIGGER human_checkpoints_no_delete BEFORE DELETE ON horus.human_checkpoints
 FOR EACH ROW EXECUTE FUNCTION horus.no_delete();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['transmissions','license_reviews'] LOOP
  EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON horus.%I FOR EACH ROW EXECUTE FUNCTION horus.no_update()',t||'_no_update',t);
  EXECUTE format('CREATE TRIGGER %I BEFORE DELETE ON horus.%I FOR EACH ROW EXECUTE FUNCTION horus.no_delete()',t||'_no_delete',t);
 END LOOP;
END $$;
-- Reutilizado exclusivamente pelo migrador ao acrescentar tabelas; nunca concedido ao app.
CREATE FUNCTION horus.protect_tables() RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE t text; read_only boolean; immutable boolean; insert_columns text;
BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='horus' LOOP
  read_only := t IN ('schema_migrations','database_identity','migration_runs');
  immutable := t IN ('transmissions','license_reviews','human_checkpoints','review_comment_events','review_comment_receipts','deletion_events','imports');
  EXECUTE format('REVOKE ALL ON horus.%I FROM PUBLIC, anon, authenticated, horus_app',t);
  EXECUTE format('ALTER TABLE horus.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE horus.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS app_select ON horus.%I',t);
  EXECUTE format('CREATE POLICY app_select ON horus.%I FOR SELECT TO horus_app USING (true)',t);
  EXECUTE format('GRANT SELECT ON horus.%I TO horus_app',t);
  IF NOT read_only THEN
   EXECUTE format('DROP POLICY IF EXISTS app_insert ON horus.%I',t);
   EXECUTE format('CREATE POLICY app_insert ON horus.%I FOR INSERT TO horus_app WITH CHECK (true)',t);
   IF EXISTS (SELECT FROM pg_attribute WHERE attrelid=format('horus.%I',t)::regclass AND attidentity<>'') THEN
    SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) INTO insert_columns
     FROM pg_attribute WHERE attrelid=format('horus.%I',t)::regclass
      AND attnum>0 AND NOT attisdropped AND attidentity='';
    EXECUTE format('GRANT INSERT(%s) ON horus.%I TO horus_app',insert_columns,t);
   ELSE
    EXECUTE format('GRANT INSERT ON horus.%I TO horus_app',t);
   END IF;
   IF NOT immutable OR t='human_checkpoints' THEN
    EXECUTE format('DROP POLICY IF EXISTS app_update ON horus.%I',t);
    EXECUTE format('CREATE POLICY app_update ON horus.%I FOR UPDATE TO horus_app USING (true) WITH CHECK (true)',t);
    IF t='human_checkpoints' THEN
     EXECUTE format('GRANT UPDATE(consumed_at,consumed_ref) ON horus.%I TO horus_app',t);
    ELSE
     EXECUTE format('GRANT UPDATE ON horus.%I TO horus_app',t);
    END IF;
   END IF;
   IF NOT immutable THEN
    EXECUTE format('DROP POLICY IF EXISTS app_delete ON horus.%I',t);
    EXECUTE format('CREATE POLICY app_delete ON horus.%I FOR DELETE TO horus_app USING (true)',t);
    EXECUTE format('GRANT DELETE ON horus.%I TO horus_app',t);
   END IF;
  END IF;
 END LOOP;
END $$;
SELECT horus.protect_tables();
GRANT USAGE ON SCHEMA horus TO horus_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA horus FROM PUBLIC, anon, authenticated, horus_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA horus TO horus_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA horus FROM PUBLIC, anon, authenticated, horus_app;
RESET ROLE;
