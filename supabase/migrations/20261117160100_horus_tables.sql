-- HOR-164: contrato legado v12, tipos PostgreSQL e canonical UTF-8.
SET ROLE horus_owner;
CREATE TABLE horus.jobs (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            payload jsonb NOT NULL, status TEXT NOT NULL,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0, result jsonb, error TEXT, workflow_id TEXT , workflow_revision INTEGER, step_key TEXT, step_index INTEGER, depends_on TEXT ,
 payload_canonical bytea NOT NULL,
 CHECK ((payload IS NULL) = (payload_canonical IS NULL)),
 CHECK (payload IS NOT DISTINCT FROM convert_from(payload_canonical,'UTF8')::jsonb));

CREATE TABLE horus.events (
            sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            job_id TEXT NOT NULL , kind TEXT NOT NULL,
            message TEXT NOT NULL, created_at timestamptz NOT NULL);

CREATE TABLE horus.media (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            size bigint NOT NULL, modified_ns bigint NOT NULL, created_at timestamptz NOT NULL);

CREATE TABLE horus.workflows (
            id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
            payload jsonb NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
            status TEXT NOT NULL CHECK(status IN ('queued','running','awaiting_approval',
                'approved','rejected','cancel_requested','cancelled','interrupted','failed')),
            feedback TEXT NOT NULL DEFAULT '', current_artifact_id TEXT ,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, executor TEXT NOT NULL DEFAULT 'simulated'
            CHECK(executor IN ('simulated','hermes')), cognitive_client TEXT NOT NULL DEFAULT 'live'
            CHECK(cognitive_client IN ('live','simulated')),
 payload_canonical bytea NOT NULL,
 CHECK ((payload IS NULL) = (payload_canonical IS NULL)),
 CHECK (payload IS NOT DISTINCT FROM convert_from(payload_canonical,'UTF8')::jsonb));

CREATE TABLE horus.job_attempts (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL ,
            number INTEGER NOT NULL CHECK(number > 0),
            status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted','cancelled')),
            executor TEXT NOT NULL, input_hash TEXT NOT NULL, input_artifacts jsonb NOT NULL,
            started_at timestamptz NOT NULL, finished_at timestamptz, result jsonb, error TEXT,
            UNIQUE(job_id, number),
 input_artifacts_canonical bytea NOT NULL,
 CHECK ((input_artifacts IS NULL) = (input_artifacts_canonical IS NULL)),
 CHECK (input_artifacts IS NOT DISTINCT FROM convert_from(input_artifacts_canonical,'UTF8')::jsonb),
 result_canonical bytea,
 CHECK ((result IS NULL) = (result_canonical IS NULL)),
 CHECK (result IS NOT DISTINCT FROM convert_from(result_canonical,'UTF8')::jsonb));

CREATE TABLE horus.artifacts (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL ,
            revision INTEGER NOT NULL, stage TEXT NOT NULL,
            job_id TEXT NOT NULL UNIQUE ,
            attempt_id TEXT NOT NULL ,
            schema_version INTEGER NOT NULL, content jsonb NOT NULL, content_hash TEXT NOT NULL,
            created_at timestamptz NOT NULL, UNIQUE(workflow_id, revision, stage),
 content_canonical bytea NOT NULL,
 CHECK ((content IS NULL) = (content_canonical IS NULL)),
 CHECK (content IS NOT DISTINCT FROM convert_from(content_canonical,'UTF8')::jsonb));

CREATE TABLE horus.approvals (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL ,
            revision INTEGER NOT NULL, artifact_id TEXT NOT NULL ,
            decision TEXT NOT NULL CHECK(decision IN ('approve','reject','request_changes')),
            feedback TEXT NOT NULL, operator_id TEXT NOT NULL, scope TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, request jsonb NOT NULL, created_at timestamptz NOT NULL,
            UNIQUE(workflow_id, idempotency_key),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb));

CREATE TABLE horus.workflow_events (
            sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            workflow_id TEXT NOT NULL , revision INTEGER NOT NULL,
            kind TEXT NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL);

CREATE TABLE horus.cognitive_runs (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL ,
            attempt_id TEXT NOT NULL ,
            provider TEXT NOT NULL CHECK(provider IN ('hermes')),
            idempotency_key TEXT NOT NULL UNIQUE, request jsonb NOT NULL, request_hash TEXT NOT NULL,
            external_ref TEXT,
            status TEXT NOT NULL CHECK(status IN ('submitting','admitted','completed','failed',
                'cancelled','interrupted','unknown','policy_violation','invalid_output')),
            remote_status TEXT, usage jsonb, error TEXT,
            created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb));

CREATE TABLE horus.authorizations (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL ,
            revision INTEGER NOT NULL, job_id TEXT NOT NULL ,
            input_artifact_id TEXT NOT NULL ,
            scope TEXT NOT NULL CHECK(scope IN ('cognitive_run')),
            max_runs INTEGER NOT NULL CHECK(max_runs = 1),
            operator_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request jsonb NOT NULL,
            consumed_attempt_id TEXT , created_at timestamptz NOT NULL,
            UNIQUE(workflow_id, idempotency_key),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb));

CREATE TABLE horus.scripts (
            id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL ,
            workflow_revision INTEGER NOT NULL, proposal_artifact_id TEXT NOT NULL ,
            proposal_content_hash TEXT NOT NULL, version INTEGER NOT NULL CHECK(version > 0),
            status TEXT NOT NULL CHECK(status IN ('awaiting_review','approved','rejected','changes_requested')),
            document jsonb NOT NULL, created_at timestamptz NOT NULL, review jsonb, origin TEXT NOT NULL DEFAULT 'simulated'
            CHECK(origin IN ('simulated','human')), generator TEXT CHECK(generator IS NULL OR generator = 'hermes'),
            UNIQUE(workflow_id, version),
 document_canonical bytea NOT NULL,
 CHECK ((document IS NULL) = (document_canonical IS NULL)),
 CHECK (document IS NOT DISTINCT FROM convert_from(document_canonical,'UTF8')::jsonb),
 review_canonical bytea,
 CHECK ((review IS NULL) = (review_canonical IS NULL)),
 CHECK (review IS NOT DISTINCT FROM convert_from(review_canonical,'UTF8')::jsonb));

CREATE TABLE horus.script_receipts (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, request jsonb NOT NULL,
            response jsonb NOT NULL, PRIMARY KEY(scope, idempotency_key),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb),
 response_canonical bytea NOT NULL,
 CHECK ((response IS NULL) = (response_canonical IS NULL)),
 CHECK (response IS NOT DISTINCT FROM convert_from(response_canonical,'UTF8')::jsonb));

CREATE TABLE horus.productions (
            id TEXT PRIMARY KEY, script_id TEXT NOT NULL ,
            script_version INTEGER NOT NULL, script_content_hash TEXT NOT NULL,
            script_review jsonb NOT NULL, job_id TEXT NOT NULL UNIQUE ,
            mode TEXT NOT NULL CHECK(mode IN ('simulated','local_demo','local_pilot')),
            plan jsonb NOT NULL, artifacts jsonb NOT NULL DEFAULT '[]', review jsonb,
            created_at timestamptz NOT NULL,
 script_review_canonical bytea NOT NULL,
 CHECK ((script_review IS NULL) = (script_review_canonical IS NULL)),
 CHECK (script_review IS NOT DISTINCT FROM convert_from(script_review_canonical,'UTF8')::jsonb),
 plan_canonical bytea NOT NULL,
 CHECK ((plan IS NULL) = (plan_canonical IS NULL)),
 CHECK (plan IS NOT DISTINCT FROM convert_from(plan_canonical,'UTF8')::jsonb),
 review_canonical bytea,
 CHECK ((review IS NULL) = (review_canonical IS NULL)),
 CHECK (review IS NOT DISTINCT FROM convert_from(review_canonical,'UTF8')::jsonb));

CREATE TABLE horus.production_receipts (
            scope TEXT NOT NULL, idempotency_key TEXT NOT NULL, request jsonb NOT NULL,
            production_id TEXT NOT NULL ,
            PRIMARY KEY(scope,idempotency_key),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb));

CREATE TABLE horus.license_reviews (
            seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            production_id TEXT NOT NULL ,
            asset_id TEXT NOT NULL, asset_sha256 TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('verified','rejected')),
            license TEXT NOT NULL, evidence TEXT NOT NULL CHECK(length(evidence) BETWEEN 1 AND 2000),
            reviewed_by TEXT NOT NULL CHECK(reviewed_by = 'human'), reviewed_at timestamptz NOT NULL,
            supersedes TEXT ,
            idempotency_key TEXT NOT NULL, request jsonb NOT NULL,
            UNIQUE(production_id, idempotency_key),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb));

CREATE TABLE horus.transmissions (
            seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id TEXT NOT NULL UNIQUE, at timestamptz NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('submit','outcome','gate_refusal','probe')),
            stage TEXT NOT NULL, workflow_id TEXT, job_id TEXT, attempt_id TEXT, run_id TEXT,
            destination TEXT, provider TEXT, model TEXT,
            envelope_bytes bigint CHECK(envelope_bytes IS NULL OR envelope_bytes >= 0),
            envelope_sha256 TEXT CHECK(envelope_sha256 IS NULL OR length(envelope_sha256) = 64),
            authorization_id TEXT,
            result TEXT NOT NULL CHECK(result IN ('attempted','sent','error','unknown','refused_gate','completed',
                'failed','cancelled','interrupted','invalid_output','policy_violation')),
            detail TEXT CHECK(detail IS NULL OR length(detail) <= 120),
            tokens_total bigint CHECK(tokens_total IS NULL OR tokens_total >= 0), cost_usd numeric,
 CHECK (cost_usd IS NULL OR (cost_usd >= 0 AND cost_usd < 'Infinity'::numeric)));

CREATE TABLE horus.human_checkpoints (
            seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, id TEXT NOT NULL UNIQUE,
            action TEXT NOT NULL CHECK(action IN ('hermes_run','hermes_script','paid_narration','publish')),
            subject TEXT NOT NULL CHECK(length(subject) = 64),
            description TEXT NOT NULL CHECK(length(description) BETWEEN 1 AND 200),
            approved_by TEXT NOT NULL CHECK(approved_by = 'human'), operator_id TEXT NOT NULL,
            approved_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            limit_amount numeric, currency TEXT CHECK(currency IS NULL OR length(currency) = 3),
            consumed_at timestamptz, consumed_ref TEXT, idempotency_key TEXT UNIQUE, request jsonb NOT NULL,
            CHECK((limit_amount IS NULL) = (currency IS NULL)),
 request_canonical bytea NOT NULL,
 CHECK ((request IS NULL) = (request_canonical IS NULL)),
 CHECK (request IS NOT DISTINCT FROM convert_from(request_canonical,'UTF8')::jsonb),
 CHECK (limit_amount IS NULL OR (limit_amount >= 0 AND limit_amount < 'Infinity'::numeric)),
 CHECK(expires_at > approved_at),
 CHECK(consumed_ref IS NULL OR length(consumed_ref) BETWEEN 1 AND 120),
 CHECK((consumed_at IS NULL) = (consumed_ref IS NULL)));
ALTER TABLE horus.jobs ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.jobs ADD FOREIGN KEY (depends_on) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.events ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.workflows ADD FOREIGN KEY (current_artifact_id) REFERENCES horus.artifacts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.job_attempts ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.artifacts ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.artifacts ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.artifacts ADD FOREIGN KEY (attempt_id) REFERENCES horus.job_attempts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.approvals ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.approvals ADD FOREIGN KEY (artifact_id) REFERENCES horus.artifacts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.workflow_events ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.cognitive_runs ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.cognitive_runs ADD FOREIGN KEY (attempt_id) REFERENCES horus.job_attempts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.authorizations ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.authorizations ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.authorizations ADD FOREIGN KEY (input_artifact_id) REFERENCES horus.artifacts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.authorizations ADD FOREIGN KEY (consumed_attempt_id) REFERENCES horus.job_attempts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.scripts ADD FOREIGN KEY (workflow_id) REFERENCES horus.workflows(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.scripts ADD FOREIGN KEY (proposal_artifact_id) REFERENCES horus.artifacts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.productions ADD FOREIGN KEY (script_id) REFERENCES horus.scripts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.productions ADD FOREIGN KEY (job_id) REFERENCES horus.jobs(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.production_receipts ADD FOREIGN KEY (production_id) REFERENCES horus.productions(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.license_reviews ADD FOREIGN KEY (production_id) REFERENCES horus.productions(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE horus.license_reviews ADD FOREIGN KEY (supersedes) REFERENCES horus.license_reviews(id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX events_job ON horus.events(job_id, sequence);
CREATE UNIQUE INDEX workflow_steps ON horus.jobs(workflow_id, workflow_revision, step_key);
CREATE INDEX workflow_event_cursor ON horus.workflow_events(workflow_id, sequence);
CREATE INDEX cognitive_runs_job ON horus.cognitive_runs(job_id, created_at);
CREATE INDEX license_reviews_asset ON horus.license_reviews(production_id, asset_id, seq);
CREATE INDEX transmissions_run ON horus.transmissions(run_id, seq);
CREATE INDEX human_checkpoints_lookup ON horus.human_checkpoints(action, subject, seq);

CREATE TABLE horus.radar_cache(key text PRIMARY KEY, fetched timestamptz NOT NULL, expires timestamptz NOT NULL, payload jsonb NOT NULL);
CREATE TABLE horus.radar_usage(id text PRIMARY KEY, at timestamptz NOT NULL, bucket text NOT NULL);
CREATE TABLE horus.radar_blocks(bucket text PRIMARY KEY, until timestamptz NOT NULL);
CREATE TABLE horus.radar_datasets(id text NOT NULL, revision integer NOT NULL CHECK(revision>0), expires timestamptz NOT NULL, collected_at timestamptz NOT NULL, payload jsonb NOT NULL, PRIMARY KEY(id,revision));
CREATE INDEX radar_cache_expires ON horus.radar_cache(expires);
CREATE INDEX radar_usage_bucket_at ON horus.radar_usage(bucket,at);
CREATE INDEX radar_datasets_expires ON horus.radar_datasets(expires);
CREATE INDEX radar_datasets_latest ON horus.radar_datasets(collected_at DESC,id);
CREATE INDEX jobs_status_created ON horus.jobs(status,created_at,id);
CREATE INDEX jobs_workflow_revision ON horus.jobs(workflow_id,workflow_revision,step_index);
CREATE INDEX workflows_created ON horus.workflows(created_at,id);
CREATE INDEX workflows_status ON horus.workflows(status);
CREATE INDEX attempts_job_started ON horus.job_attempts(job_id,started_at);
CREATE INDEX artifacts_workflow_revision ON horus.artifacts(workflow_id,revision);
CREATE INDEX approvals_workflow_created ON horus.approvals(workflow_id,created_at);
CREATE INDEX runs_status ON horus.cognitive_runs(status);
CREATE INDEX authorizations_job_revision ON horus.authorizations(job_id,revision);
CREATE INDEX productions_script_created ON horus.productions(script_id,created_at,id);
CREATE INDEX production_receipts_production ON horus.production_receipts(production_id);

-- JSON operacional é documento/coleção, nunca scalar ou JSON null.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT table_name,column_name FROM information_schema.columns
  WHERE table_schema='horus' AND data_type='jsonb' LOOP
  EXECUTE format('ALTER TABLE horus.%I ADD CHECK (%I IS NULL OR jsonb_typeof(%I) IN (''object'',''array''))',c.table_name,c.column_name,c.column_name);
 END LOOP;
END $$;
RESET ROLE;
