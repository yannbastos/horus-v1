-- HOR-139: schema dedicado; não altera tabelas operacionais nem configura a Data API.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='horus_sync_service') THEN
    CREATE ROLE horus_sync_service NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='horus_sync_login') THEN
    -- Sem senha: provisioning seguro posterior, exclusivamente no servidor.
    CREATE ROLE horus_sync_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('horus_sync_service','horus_sync_login')
             AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)) THEN
    RAISE EXCEPTION 'Papel preexistente incompatível com least privilege';
  END IF;
END $$;
GRANT horus_sync_service TO horus_sync_login;
CREATE SCHEMA horus_sync;
REVOKE ALL ON SCHEMA horus_sync FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA horus_sync TO horus_sync_service;
-- Sem mudar defaults globais: o schema privado e os revokes explícitos fecham funções.
ALTER DEFAULT PRIVILEGES IN SCHEMA horus_sync REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA horus_sync REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA horus_sync REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

-- Lê somente o envelope transacional: ignora request.jwt.claim.sub legado do pooler.
CREATE FUNCTION horus_sync.current_owner() RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE claims jsonb := nullif(current_setting('request.jwt.claims',true),'')::jsonb;
BEGIN
  IF claims->>'aud' IS DISTINCT FROM 'authenticated'
      OR claims->>'role' IS DISTINCT FROM 'authenticated' THEN RETURN NULL; END IF;
  RETURN (claims->>'sub')::uuid;
END $$;

CREATE TABLE horus_sync.identities (
  owner_id uuid PRIMARY KEY REFERENCES auth.users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE horus_sync.workspaces (
  owner_id uuid NOT NULL REFERENCES horus_sync.identities(owner_id),
  id uuid NOT NULL,
  label text NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
  cursor bigint NOT NULL DEFAULT 0 CHECK (cursor >= 0),
  PRIMARY KEY (owner_id,id)
);
CREATE TABLE horus_sync.revisions (
  owner_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  entity_id uuid NOT NULL,
  server_version bigint NOT NULL CHECK (server_version > 0),
  base_version bigint NOT NULL CHECK (base_version >= 0 AND server_version = base_version + 1),
  cursor bigint NOT NULL CHECK (cursor > 0),
  kind text NOT NULL CHECK (kind IN ('workflow','script','comment','review_history')),
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text) <= 32768),
  deleted boolean NOT NULL DEFAULT false,
  content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (owner_id,entity_id,server_version),
  UNIQUE(owner_id,workspace_id,cursor),
  FOREIGN KEY(owner_id,workspace_id) REFERENCES horus_sync.workspaces(owner_id,id),
  CHECK (payload - ARRAY['title','text','historical_only'] = '{}'::jsonb),
  CHECK ((NOT payload ? 'title' OR jsonb_typeof(payload->'title')='string')
     AND (NOT payload ? 'text' OR jsonb_typeof(payload->'text')='string')),
  CHECK (kind <> 'review_history' OR coalesce(payload->'historical_only' = 'true'::jsonb,false) OR deleted),
  CHECK (NOT deleted OR payload='{}'::jsonb)
);
CREATE TABLE horus_sync.receipts (
  owner_id uuid NOT NULL,
  operation_id uuid NOT NULL,
  workspace_id uuid NOT NULL,
  entity_id uuid NOT NULL,
  server_version bigint NOT NULL,
  request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(owner_id,operation_id),
  FOREIGN KEY(owner_id,workspace_id) REFERENCES horus_sync.workspaces(owner_id,id),
  FOREIGN KEY(owner_id,entity_id,server_version) REFERENCES horus_sync.revisions(owner_id,entity_id,server_version)
);
CREATE INDEX receipts_workspace ON horus_sync.receipts(owner_id,workspace_id,created_at);

-- Hash sempre refeito no banco, mesmo em INSERT direto pelo papel do serviço.
CREATE FUNCTION horus_sync.hash_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
  NEW.content_hash := encode(sha256(convert_to(NEW.payload::text,'UTF8')),'hex');
  RETURN NEW;
END $$;
CREATE TRIGGER revision_hash BEFORE INSERT ON horus_sync.revisions
  FOR EACH ROW EXECUTE FUNCTION horus_sync.hash_revision();

-- Sem políticas de exclusão: retenção/apagamento pertence à manutenção administrativa,
-- fora do serviço. Recibos/tombstones retidos até snapshot/ack de todos os dispositivos.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['identities','workspaces','revisions','receipts'] LOOP
    EXECUTE format('ALTER TABLE horus_sync.%I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE horus_sync.%I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY own_select ON horus_sync.%I FOR SELECT TO horus_sync_service USING (owner_id = (SELECT horus_sync.current_owner()))',t);
    EXECUTE format('CREATE POLICY own_insert ON horus_sync.%I FOR INSERT TO horus_sync_service WITH CHECK (owner_id = (SELECT horus_sync.current_owner()))',t);
  END LOOP;
END $$;
CREATE POLICY own_update ON horus_sync.workspaces FOR UPDATE TO horus_sync_service
  USING (owner_id = (SELECT horus_sync.current_owner())) WITH CHECK (owner_id = (SELECT horus_sync.current_owner()));
REVOKE ALL ON ALL TABLES IN SCHEMA horus_sync FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA horus_sync TO horus_sync_service;
GRANT UPDATE(cursor) ON horus_sync.workspaces TO horus_sync_service;

-- Invoker: não eleva privilégios; o servidor remoto valida JWT antes de definir claims.
CREATE FUNCTION horus_sync.submit_revision(
  p_operation uuid, p_workspace uuid, p_entity uuid, p_base bigint,
  p_kind text, p_payload jsonb, p_deleted boolean DEFAULT false
) RETURNS horus_sync.receipts LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, horus_sync AS $$
DECLARE
  who uuid := horus_sync.current_owner();
  prior horus_sync.receipts;
  result horus_sync.receipts;
  version_now bigint;
  next_cursor bigint;
  calculated text;
BEGIN
  IF who IS NULL THEN RAISE EXCEPTION 'unauthenticated' USING ERRCODE='28000'; END IF;
  IF p_operation IS NULL OR p_entity IS NULL OR p_base IS NULL OR p_base < 0 OR p_kind IS NULL
      OR p_payload IS NULL OR p_deleted IS NULL THEN
    RAISE EXCEPTION 'invalid envelope' USING ERRCODE='22023';
  END IF;
  -- Serializa o feed do workspace até COMMIT: cursor não pula commits tardios.
  SELECT cursor INTO next_cursor FROM horus_sync.workspaces
    WHERE owner_id=who AND id=p_workspace FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'workspace forbidden' USING ERRCODE='42501'; END IF;
  -- JSONB é a representação canônica do servidor, jamais um hash confiado ao cliente.
  calculated := encode(sha256(convert_to(jsonb_build_object('workspace',p_workspace,'entity',p_entity,
      'base',p_base,'kind',p_kind,'payload',p_payload,'deleted',p_deleted)::text,'UTF8')),'hex');
  SELECT * INTO prior FROM horus_sync.receipts WHERE owner_id=who AND operation_id=p_operation;
  IF FOUND THEN
    IF prior.request_hash <> calculated THEN RAISE EXCEPTION 'operation conflict' USING ERRCODE='40001'; END IF;
    RETURN prior;
  END IF;
  IF EXISTS (SELECT FROM horus_sync.revisions WHERE owner_id=who AND entity_id=p_entity AND workspace_id<>p_workspace) THEN
    RAISE EXCEPTION 'entity workspace conflict' USING ERRCODE='40001';
  END IF;
  SELECT coalesce(max(server_version),0) INTO version_now FROM horus_sync.revisions
    WHERE owner_id=who AND entity_id=p_entity;
  IF version_now <> p_base THEN RAISE EXCEPTION 'version conflict' USING ERRCODE='40001'; END IF;
  next_cursor := next_cursor + 1;
  INSERT INTO horus_sync.revisions(owner_id,workspace_id,entity_id,server_version,base_version,cursor,kind,payload,deleted)
    VALUES(who,p_workspace,p_entity,version_now+1,p_base,next_cursor,p_kind,p_payload,p_deleted);
  INSERT INTO horus_sync.receipts(owner_id,operation_id,workspace_id,entity_id,server_version,request_hash)
    VALUES(who,p_operation,p_workspace,p_entity,version_now+1,calculated) RETURNING * INTO result;
  UPDATE horus_sync.workspaces SET cursor=next_cursor WHERE owner_id=who AND id=p_workspace;
  RETURN result;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA horus_sync FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION horus_sync.submit_revision(uuid,uuid,uuid,bigint,text,jsonb,boolean) TO horus_sync_service;

GRANT EXECUTE ON FUNCTION horus_sync.current_owner() TO horus_sync_service;
