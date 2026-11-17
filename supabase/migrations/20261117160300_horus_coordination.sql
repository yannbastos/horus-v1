-- HOR-164: estado JSON operacional e coordenação; não guarda credenciais.
SET ROLE horus_owner;
CREATE TABLE horus.migration_runs(
 run_id uuid PRIMARY KEY, source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 mapping_version text NOT NULL, source_schema_version integer NOT NULL CHECK(source_schema_version>0),
 table_checks jsonb NOT NULL CHECK(jsonb_typeof(table_checks)='object'), applied_at timestamptz NOT NULL);
CREATE TABLE horus.worker_instances(
 id uuid PRIMARY KEY, device_id text NOT NULL, lease_until timestamptz NOT NULL,
 fencing bigint NOT NULL CHECK(fencing>0));
ALTER TABLE horus.jobs ADD COLUMN worker_instance_id uuid REFERENCES horus.worker_instances(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.jobs ADD COLUMN lease_until timestamptz;
ALTER TABLE horus.jobs ADD COLUMN fencing_token bigint CHECK(fencing_token>0);
ALTER TABLE horus.jobs ADD CONSTRAINT jobs_claim_complete CHECK(
 (worker_instance_id IS NULL AND lease_until IS NULL AND fencing_token IS NULL) OR
 (worker_instance_id IS NOT NULL AND lease_until IS NOT NULL AND fencing_token IS NOT NULL));
CREATE INDEX jobs_lease ON horus.jobs(lease_until) WHERE worker_instance_id IS NOT NULL;
CREATE INDEX workers_lease ON horus.worker_instances(lease_until);
CREATE TABLE horus.review_comments(
 id text PRIMARY KEY, production_id text NOT NULL REFERENCES horus.productions(id) DEFERRABLE INITIALLY DEFERRED,
 artifact_sha256 text NOT NULL CHECK(artifact_sha256 ~ '^[0-9a-f]{64}$'),
 at_seconds numeric NOT NULL CHECK(at_seconds BETWEEN 0 AND 86400),
 text text NOT NULL CHECK(length(text) BETWEEN 1 AND 2000), author text NOT NULL CHECK(length(author) BETWEEN 1 AND 80), author_kind text NOT NULL CHECK(author_kind='human'),
 status text NOT NULL CHECK(status IN ('open','resolved')), created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
CREATE INDEX comments_production_time ON horus.review_comments(production_id,at_seconds,id);
CREATE TABLE horus.review_comment_events(
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 comment_id text NOT NULL REFERENCES horus.review_comments(id) DEFERRABLE INITIALLY DEFERRED,
 action text NOT NULL CHECK(action IN ('created','resolved','reopened')), operator_id text NOT NULL,
 created_at timestamptz NOT NULL, data jsonb NOT NULL CHECK(jsonb_typeof(data)='object'));
CREATE INDEX comment_events_cursor ON horus.review_comment_events(comment_id,seq);
CREATE TABLE horus.review_comment_receipts(
 production_id text NOT NULL REFERENCES horus.productions(id) DEFERRABLE INITIALLY DEFERRED,
 scope text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'), response_canonical bytea NOT NULL,
 CHECK(response=convert_from(response_canonical,'UTF8')::jsonb), PRIMARY KEY(production_id,scope,idempotency_key));
CREATE TABLE horus.notification_state(
 device_id text NOT NULL, event_id text NOT NULL, state text NOT NULL CHECK(state IN ('read','unread')),
 changed_at timestamptz NOT NULL, PRIMARY KEY(device_id,event_id));
-- Watermark read_all_before é um valor tipado/validado pelo repositório, por dispositivo.
CREATE TABLE horus.preferences(
 device_id text NOT NULL, key text NOT NULL, value jsonb NOT NULL,
 updated_at timestamptz NOT NULL, PRIMARY KEY(device_id,key));
CREATE TABLE horus.deletion_events(
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id text NOT NULL UNIQUE, audit_id text NOT NULL,
 action text NOT NULL, target text NOT NULL, device_id text NOT NULL, created_at timestamptz NOT NULL,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'), payload_canonical bytea NOT NULL,
 prev_hash text CHECK(prev_hash ~ '^[0-9a-f]{64}$'), hash text NOT NULL CHECK(hash ~ '^[0-9a-f]{64}$'),
 CHECK(payload=convert_from(payload_canonical,'UTF8')::jsonb));
CREATE INDEX deletion_audit ON horus.deletion_events(audit_id,seq);
CREATE TRIGGER deletion_events_append_lock BEFORE INSERT ON horus.deletion_events
 FOR EACH STATEMENT EXECUTE FUNCTION horus.append_lock();
-- payload_canonical contém o evento original sem o campo hash, como no legado.
CREATE FUNCTION horus.deletion_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE previous text;
BEGIN
 SELECT hash INTO previous FROM horus.deletion_events ORDER BY seq DESC LIMIT 1;
 IF NEW.prev_hash IS DISTINCT FROM coalesce(previous,repeat('0',64))
  OR NEW.hash IS DISTINCT FROM encode(sha256(NEW.payload_canonical),'hex') THEN
  RAISE EXCEPTION 'Cadeia de exclusão inválida' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER deletion_events_chain BEFORE INSERT ON horus.deletion_events
 FOR EACH ROW EXECUTE FUNCTION horus.deletion_chain();
CREATE TABLE horus.imports(
 id text PRIMARY KEY, device_id text NOT NULL, path text NOT NULL,
 hash text NOT NULL CHECK(hash ~ '^[0-9a-f]{64}$'), manifest jsonb NOT NULL CHECK(jsonb_typeof(manifest)='object'),
 created_at timestamptz NOT NULL, approved boolean NOT NULL DEFAULT false CHECK(NOT approved),
 publishable boolean NOT NULL DEFAULT false CHECK(NOT publishable), UNIQUE(device_id,path));
CREATE TABLE horus.publishing_receipts(
 production_id text NOT NULL REFERENCES horus.productions(id) DEFERRABLE INITIALLY DEFERRED,
 scope text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 response jsonb NOT NULL CHECK(jsonb_typeof(response)='object'), response_canonical bytea NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','succeeded','failed','unknown')),
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 CHECK(response=convert_from(response_canonical,'UTF8')::jsonb), PRIMARY KEY(production_id,scope,idempotency_key));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['review_comment_events','review_comment_receipts','deletion_events','imports'] LOOP
  EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON horus.%I FOR EACH ROW EXECUTE FUNCTION horus.no_update()',t||'_no_update',t);
  EXECUTE format('CREATE TRIGGER %I BEFORE DELETE ON horus.%I FOR EACH ROW EXECUTE FUNCTION horus.no_delete()',t||'_no_delete',t);
 END LOOP;
END $$;
SELECT horus.protect_tables();
REVOKE ALL ON ALL SEQUENCES IN SCHEMA horus FROM PUBLIC, anon, authenticated, horus_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA horus TO horus_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA horus FROM PUBLIC, anon, authenticated, horus_app;
RESET ROLE;
