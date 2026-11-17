"""Contrato HOR-164/I2: DDL real, privilégios negativos e concorrência em PG local."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import ast
import threading

import psycopg
import pytest
from schema_support import ROOT, HORUS_MIGRATIONS, apply_chain, schema_database, migration_checksum

ACTIVE = set('jobs events media workflows job_attempts artifacts approvals workflow_events cognitive_runs authorizations scripts script_receipts productions production_receipts license_reviews transmissions human_checkpoints radar_cache radar_usage radar_blocks radar_datasets'.split())
ADDITIONAL = set('database_identity schema_migrations migration_runs worker_instances review_comments review_comment_events review_comment_receipts notification_state preferences deletion_events imports publishing_receipts'.split())


@pytest.fixture(scope='module')
def _schema_database(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def schema(request, pg_cluster):
    if request.node.get_closest_marker('pg_strong'):
        with schema_database(pg_cluster) as state:
            yield state
        return
    admin, pool = request.getfixturevalue('_schema_database')
    try:
        yield admin, pool
    finally:
        targets = sorted((ACTIVE | ADDITIONAL) - {'schema_migrations','database_identity'})
        admin.execute('TRUNCATE '+','.join('horus.'+t for t in targets)+' RESTART IDENTITY CASCADE')


def checkpoint(conn, identifier='checkpoint'):
    return conn.execute("""INSERT INTO horus.human_checkpoints
      (id,action,subject,description,approved_by,operator_id,approved_at,expires_at,request,request_canonical)
      VALUES(%s,'publish',%s,'Sintético','human','teste',clock_timestamp()-interval '1 minute',
       clock_timestamp()+interval '1 hour','{}',%s) RETURNING seq""", (identifier,'a'*64,b'{}')).fetchone()[0]


def test_schema_fresh_and_idempotent(schema):
    admin, pool = schema
    assert {r[0] for r in admin.execute("SELECT tablename FROM pg_tables WHERE schemaname='horus'")} == ACTIVE | ADDITIONAL
    identity = admin.execute('SELECT * FROM horus.database_identity').fetchall()
    apply_chain(admin)
    assert admin.execute('SELECT * FROM horus.database_identity').fetchall() == identity
    assert admin.execute('SELECT version,checksum FROM horus.schema_migrations ORDER BY version').fetchall() == [(p.name.split('_')[0],migration_checksum(p)) for p in HORUS_MIGRATIONS]
    assert admin.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='horus'").fetchall() == [('horus_owner',)]


@pytest.mark.parametrize('table', sorted(ACTIVE | ADDITIONAL))
def test_schema_every_table_security(schema, table):
    admin, pool = schema
    assert admin.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ('horus.'+table,)).fetchone() == (True,True)
    roles = admin.execute("SELECT roles FROM pg_policies WHERE schemaname='horus' AND tablename=%s", (table,)).fetchall()
    assert roles and all(r[0] == ['horus_app'] for r in roles)
    for role in ('anon','authenticated','public'):
        # PUBLIC é um pseudo-papel; a ACL é verificada por expansão.
        assert not admin.execute("SELECT EXISTS(SELECT FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE c.oid=%s::regclass AND a.grantee=CASE WHEN %s='public' THEN 0 ELSE (SELECT oid FROM pg_roles WHERE rolname=%s) END)", ('horus.'+table,role,role)).fetchone()[0]
    assert not admin.execute("SELECT has_table_privilege('horus_app',%s,'TRUNCATE,REFERENCES,TRIGGER')", ('horus.'+table,)).fetchone()[0]


@pytest.mark.parametrize('table,column,typ', [
 ('jobs','id','text'),('jobs','payload','jsonb'),('jobs','payload_canonical','bytea'),
 ('jobs','created_at','timestamp with time zone'),('jobs','attempt','integer'),
 ('jobs','fencing_token','bigint'),('media','modified_ns','bigint'),
 ('transmissions','cost_usd','numeric'),('human_checkpoints','limit_amount','numeric'),
 ('productions','plan_canonical','bytea'),('scripts','review_canonical','bytea'),
 ('radar_usage','at','timestamp with time zone'),('radar_blocks','until','timestamp with time zone'),
 ('database_identity','instance_id','uuid'),('migration_runs','table_checks','jsonb')])
def test_schema_column_types(schema, table, column, typ):
    admin, _ = schema
    assert admin.execute("SELECT data_type FROM information_schema.columns WHERE table_schema='horus' AND table_name=%s AND column_name=%s", (table,column)).fetchone() == (typ,)


def test_schema_roles_and_functions(schema):
    admin,pool=schema
    assert admin.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname='horus_app'").fetchone() == (False,)*5
    assert not admin.execute("SELECT pg_has_role('horus_app','horus_owner','MEMBER')").fetchone()[0]
    assert admin.execute("SELECT bool_and(NOT prosecdef AND proconfig=ARRAY['search_path=pg_catalog']) FROM pg_proc WHERE pronamespace='horus'::regnamespace").fetchone()[0]
    assert not admin.execute("SELECT EXISTS(SELECT FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) a WHERE p.pronamespace='horus'::regnamespace AND a.grantee=0)").fetchone()[0]
    with pool.connection() as app:
        assert app.execute('SELECT count(*) FROM horus.database_identity').fetchone() == (1,)
        for statement in ('CREATE SCHEMA evil','CREATE TABLE horus.evil(id int)','CREATE TABLE public.evil(id int)','CREATE TEMP TABLE evil(id int)', 'TRUNCATE horus.media', 'SET ROLE horus_owner', 'SELECT * FROM auth.users','SELECT * FROM horus_sync.identities','SELECT horus.protect_tables()',"INSERT INTO horus.schema_migrations VALUES('bad','bad',now(),NULL,NULL)"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege): app.execute(statement)


@pytest.mark.parametrize('table', ['transmissions','license_reviews','human_checkpoints'])
@pytest.mark.parametrize('operation', ['UPDATE','DELETE'])
def test_schema_append_only_trigger(schema, table, operation):
    admin,pool=schema
    if table=='human_checkpoints': checkpoint(admin)
    elif table=='transmissions': admin.execute("INSERT INTO horus.transmissions(id,at,kind,stage,result) VALUES('t',now(),'probe','test','attempted')")
    else:
        # A FK diferida permite testar o trigger; o caso inteiro sofre rollback.
        with admin.transaction(force_rollback=True):
            admin.execute("INSERT INTO horus.license_reviews(id,production_id,asset_id,asset_sha256,decision,license,evidence,reviewed_by,reviewed_at,idempotency_key,request,request_canonical) VALUES('l','p','a','hash','verified','CC0','test','human',now(),'k','{}',%s)",(b'{}',))
            with pytest.raises(psycopg.errors.CheckViolation):
                with admin.transaction(): admin.execute(f"{operation if operation=='UPDATE' else 'DELETE FROM'} horus.{table}" + (" SET id=id" if operation=='UPDATE' else ''))
        return
    statement=f"{operation if operation=='UPDATE' else 'DELETE FROM'} horus.{table}" + (' SET id=id' if operation=='UPDATE' else '')
    with pytest.raises(psycopg.errors.CheckViolation): admin.execute(statement)
    with pool.connection() as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege): app.execute(statement)


@pytest.mark.pg_strong
def test_schema_checkpoint_two_sessions(schema):
    admin,pool=schema
    checkpoint(admin)
    barrier=threading.Barrier(2)
    def consume(ref):
        with pool.connection() as app:
            barrier.wait(timeout=5)
            with app.transaction():
                return app.execute("UPDATE horus.human_checkpoints SET consumed_at=clock_timestamp(),consumed_ref=%s WHERE id='checkpoint' AND consumed_at IS NULL AND expires_at>clock_timestamp() RETURNING id",(ref,)).fetchall()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(consume,['a','b']))
    assert sorted(map(len,results)) == [0,1]
    with pool.connection() as app:
        with pytest.raises(psycopg.errors.CheckViolation): app.execute("UPDATE horus.human_checkpoints SET consumed_ref='twice' WHERE id='checkpoint'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege): app.execute("UPDATE horus.human_checkpoints SET description='changed' WHERE id='checkpoint'")


def test_schema_identity_override_and_reseed(schema):
    admin,pool=schema
    with pytest.raises(psycopg.errors.GeneratedAlways): admin.execute("INSERT INTO horus.transmissions(seq,id,at,kind,stage,result) VALUES(90,'t',now(),'probe','test','attempted')")
    admin.execute("INSERT INTO horus.transmissions(seq,id,at,kind,stage,result) OVERRIDING SYSTEM VALUE VALUES(90,'t',now(),'probe','test','attempted')")
    admin.execute("SELECT setval(pg_get_serial_sequence('horus.transmissions','seq'),90,true)")
    with pool.connection() as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("INSERT INTO horus.transmissions(seq,id,at,kind,stage,result) OVERRIDING SYSTEM VALUE VALUES(999,'forbidden',now(),'probe','test','attempted')")
        assert app.execute("INSERT INTO horus.transmissions(id,at,kind,stage,result) VALUES('next',now(),'probe','test','attempted') RETURNING seq").fetchone() == (91,)


def test_schema_expected_version_contract():
    path=ROOT/'backend/src/horus_backend/database/expected_schema.py'
    if not path.exists(): pytest.skip('Contrato cruzado I1 pendente de integração: expected_schema.py ausente')
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,(ast.Assign,ast.AnnAssign)) and any(isinstance(t,ast.Name) and t.id=='EXPECTED_SCHEMA_VERSION' for t in (n.targets if isinstance(n,ast.Assign) else [n.target]))]
    assert values == [HORUS_MIGRATIONS[-1].name.split('_')[0]]

# Manifesto explícito: evita omissões silenciosas ao acrescentar/renomear colunas.
COLUMNS = {
 'jobs':'id idempotency_key payload status created_at updated_at attempt result error workflow_id workflow_revision step_key step_index depends_on payload_canonical worker_instance_id lease_until fencing_token',
 'events':'sequence job_id kind message created_at',
 'media':'id path name size modified_ns created_at',
 'workflows':'id idempotency_key payload revision status feedback current_artifact_id created_at updated_at executor cognitive_client payload_canonical',
 'job_attempts':'id job_id number status executor input_hash input_artifacts started_at finished_at result error input_artifacts_canonical result_canonical',
 'artifacts':'id workflow_id revision stage job_id attempt_id schema_version content content_hash created_at content_canonical',
 'approvals':'id workflow_id revision artifact_id decision feedback operator_id scope idempotency_key request created_at request_canonical',
 'workflow_events':'sequence workflow_id revision kind data created_at',
 'cognitive_runs':'id job_id attempt_id provider idempotency_key request request_hash external_ref status remote_status usage error created_at updated_at request_canonical',
 'authorizations':'id workflow_id revision job_id input_artifact_id scope max_runs operator_id idempotency_key request consumed_attempt_id created_at request_canonical',
 'scripts':'id workflow_id workflow_revision proposal_artifact_id proposal_content_hash version status document created_at review origin generator document_canonical review_canonical',
 'script_receipts':'scope idempotency_key request response request_canonical response_canonical',
 'productions':'id script_id script_version script_content_hash script_review job_id mode plan artifacts review created_at script_review_canonical plan_canonical review_canonical',
 'production_receipts':'scope idempotency_key request production_id request_canonical',
 'license_reviews':'seq id production_id asset_id asset_sha256 decision license evidence reviewed_by reviewed_at supersedes idempotency_key request request_canonical',
 'transmissions':'seq id at kind stage workflow_id job_id attempt_id run_id destination provider model envelope_bytes envelope_sha256 authorization_id result detail tokens_total cost_usd',
 'human_checkpoints':'seq id action subject description approved_by operator_id approved_at expires_at limit_amount currency consumed_at consumed_ref idempotency_key request request_canonical',
 'radar_cache':'key fetched expires payload', 'radar_usage':'id at bucket', 'radar_blocks':'bucket until',
 'radar_datasets':'id revision expires collected_at payload',
 'database_identity':'singleton instance_id created_at', 'schema_migrations':'version checksum applied_at app_min app_max',
 'migration_runs':'run_id source_sha256 mapping_version source_schema_version table_checks applied_at',
 'worker_instances':'id device_id lease_until fencing',
 'review_comments':'id production_id artifact_sha256 at_seconds text author author_kind status created_at updated_at',
 'review_comment_events':'seq comment_id action operator_id created_at data',
 'review_comment_receipts':'production_id scope idempotency_key request_hash response response_canonical',
 'notification_state':'device_id event_id state changed_at', 'preferences':'device_id key value updated_at',
 'deletion_events':'seq id audit_id action target device_id created_at payload payload_canonical prev_hash hash',
 'imports':'id device_id path hash manifest created_at approved publishable',
 'publishing_receipts':'production_id scope idempotency_key request_hash response response_canonical status created_at updated_at',
}


def test_schema_all_columns_and_identity(schema):
    admin,_=schema
    actual={}
    for table,column in admin.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='horus'"):
        actual.setdefault(table,set()).add(column)
    assert actual == {t:set(cols.split()) for t,cols in COLUMNS.items()}
    identities=admin.execute("SELECT table_name,column_name,identity_generation FROM information_schema.columns WHERE table_schema='horus' AND is_identity='YES'").fetchall()
    assert {(t,c) for t,c,g in identities} == {('events','sequence'),('workflow_events','sequence'),('license_reviews','seq'),('transmissions','seq'),('human_checkpoints','seq'),('review_comment_events','seq'),('deletion_events','seq')}
    assert all(g=='ALWAYS' for t,c,g in identities)
    assert admin.execute("SELECT bool_and(condeferrable AND condeferred) FROM pg_constraint WHERE connamespace='horus'::regnamespace AND contype='f'").fetchone()[0]


def graph(conn):
    """Carga fora de ordem: workflow aponta ao artifact que ainda será inserido."""
    conn.execute("""INSERT INTO horus.workflows(id,idempotency_key,payload,payload_canonical,revision,status,current_artifact_id,created_at,updated_at)
      VALUES('w','w','{}',%s,1,'queued','a',now(),now())""",(b'{}',))
    conn.execute("""INSERT INTO horus.jobs(id,idempotency_key,payload,payload_canonical,status,created_at,updated_at,workflow_id)
      VALUES('j','j','{}',%s,'queued',now(),now(),'w'),('p-job','p-job','{}',%s,'queued',now(),now(),'w')""",(b'{}',b'{}'))
    conn.execute("""INSERT INTO horus.job_attempts(id,job_id,number,status,executor,input_hash,input_artifacts,input_artifacts_canonical,started_at)
      VALUES('attempt','j',1,'running','simulated','hash','[]',%s,now())""",(b'[]',))
    conn.execute("""INSERT INTO horus.artifacts(id,workflow_id,revision,stage,job_id,attempt_id,schema_version,content,content_canonical,content_hash,created_at)
      VALUES('a','w',1,'proposal','j','attempt',1,'{}',%s,'hash',now())""",(b'{}',))
    conn.execute("""INSERT INTO horus.scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,version,status,document,document_canonical,created_at)
      VALUES('s','w',1,'a','hash',1,'approved','{}',%s,now())""",(b'{}',))
    conn.execute("""INSERT INTO horus.productions(id,script_id,script_version,script_content_hash,script_review,script_review_canonical,job_id,mode,plan,plan_canonical,created_at)
      VALUES('p','s',1,'hash','{}',%s,'p-job','local_pilot','{}',%s,now())""",(b'{}',b'{}'))


def test_schema_cyclic_graph_local_pilot_and_integrity(schema):
    admin,pool=schema
    with pool.connection() as app:
        with app.transaction():
            graph(app)
            app.execute('SET CONSTRAINTS ALL IMMEDIATE')
        assert app.execute('SELECT current_artifact_id FROM horus.workflows').fetchone() == ('a',)
        with pytest.raises(psycopg.errors.CheckViolation): app.execute("UPDATE horus.productions SET mode='invalid'")
        with pytest.raises(psycopg.errors.UniqueViolation): app.execute("INSERT INTO horus.media VALUES('m','path','a',1,1,now()),('n','path','b',1,1,now())")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with app.transaction(): app.execute("INSERT INTO horus.events(job_id,kind,message,created_at) VALUES('missing','test','test',now())")
        with pytest.raises(psycopg.errors.ForeignKeyViolation): app.execute("DELETE FROM horus.jobs WHERE id='j'")


@pytest.mark.parametrize('json_value,canonical', [('{}',b'{"different":true}'),('null',b'null'),('1',b'1'),('{}',None)])
def test_schema_json_canonical_rejects_divergence(schema,json_value,canonical):
    _,pool=schema
    with pool.connection() as app:
        with pytest.raises((psycopg.errors.CheckViolation,psycopg.errors.NotNullViolation)):
            app.execute("INSERT INTO horus.jobs(id,idempotency_key,payload,payload_canonical,status,created_at,updated_at) VALUES('j','j',%s,%s,'queued',now(),now())",(json_value,canonical))


def test_schema_canonical_preserves_legacy_numeric_bytes(schema):
    _,pool=schema
    with pool.connection() as app:
        app.execute("INSERT INTO horus.jobs(id,idempotency_key,payload,payload_canonical,status,created_at,updated_at) VALUES('one','one','{\"n\":1}',%s,'queued',now(),now()),('float','float','{\"n\":1.0}',%s,'queued',now(),now())",(b'{"n":1}',b'{"n":1.0}'))
        assert app.execute("SELECT a.payload=b.payload,a.payload_canonical=b.payload_canonical FROM horus.jobs a,horus.jobs b WHERE a.id='one' AND b.id='float'").fetchone() == (True,False)


@pytest.mark.parametrize('change', ["expires_at=approved_at", "limit_amount=-1,currency='USD'", "limit_amount='NaN',currency='USD'", "limit_amount=1", "subject='short'", "approved_by='agent'"])
def test_schema_checkpoint_checks(schema,change):
    admin,_=schema
    checkpoint(admin)
    # Trigger de consumo não pode mascarar a constraint específica deste teste.
    # INSERT SELECT sobre linha válida mantém todas as demais invariantes.
    columns=[c for c in COLUMNS['human_checkpoints'].split() if c not in ('seq','id')]
    replacements=dict(part.split('=',1) for part in change.split(','))
    values=[replacements.get(c,c) for c in columns]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute('INSERT INTO horus.human_checkpoints(id,'+','.join(columns)+") SELECT 'invalid',"+','.join(values)+' FROM horus.human_checkpoints')


@pytest.mark.parametrize('change', ["consumed_at=expires_at,consumed_ref='r'", "consumed_at=approved_at-interval '1 second',consumed_ref='r'", "consumed_at=now()", "consumed_at=now(),consumed_ref=repeat('x',121)"])
def test_schema_checkpoint_consumption_invalid(schema,change):
    admin,pool=schema
    checkpoint(admin)
    with pool.connection() as app:
        with pytest.raises(psycopg.errors.CheckViolation): app.execute('UPDATE horus.human_checkpoints SET '+change)


def test_schema_rls_owner_forced_and_other_roles_denied(schema):
    admin,_=schema
    with admin.transaction():
        admin.execute('SET LOCAL ROLE horus_owner')
        assert admin.execute('SELECT * FROM horus.database_identity').fetchall() == []
    for role in ('anon','authenticated'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with admin.transaction():
                admin.execute('SET LOCAL ROLE '+role)
                admin.execute('SELECT * FROM horus.database_identity')


def test_schema_checksum_mismatch_refused(schema):
    admin,_=schema
    with admin.transaction(force_rollback=True):
        admin.execute("UPDATE horus.schema_migrations SET checksum=repeat('0',64) WHERE version=%s",(HORUS_MIGRATIONS[-1].name.split('_')[0],))
        with pytest.raises(ValueError,match='Checksum'): apply_chain(admin)


def test_schema_deletion_chain_and_audit_append_only(schema):
    admin,pool=schema
    digest=sha256(b'{}').hexdigest()
    with pool.connection() as app:
        app.execute("INSERT INTO horus.deletion_events(id,audit_id,action,target,device_id,created_at,payload,payload_canonical,prev_hash,hash) VALUES('d','audit','started','synthetic','test',now(),'{}',%s,%s,%s)",(b'{}','0'*64,digest))
        with pytest.raises(psycopg.errors.CheckViolation):
            app.execute("INSERT INTO horus.deletion_events(id,audit_id,action,target,device_id,created_at,payload,payload_canonical,prev_hash,hash) VALUES('bad','audit','completed','synthetic','test',now(),'{}',%s,%s,%s)",(b'{}','0'*64,digest))
        app.execute("INSERT INTO horus.deletion_events(id,audit_id,action,target,device_id,created_at,payload,payload_canonical,prev_hash,hash) VALUES('next','audit','completed','synthetic','test',now(),'{}',%s,%s,%s)",(b'{}',digest,digest))
    for statement in ("UPDATE horus.deletion_events SET action='changed'",'DELETE FROM horus.deletion_events'):
        with pytest.raises(psycopg.errors.CheckViolation): admin.execute(statement)


def test_schema_types_for_every_column(schema):
    admin,_=schema
    json_names=set('payload result input_artifacts content request data usage document review response script_review plan artifacts table_checks value manifest'.split())
    integers=set('attempt workflow_revision step_index revision number schema_version max_runs version script_version source_schema_version'.split())
    bigints=set('sequence seq size modified_ns envelope_bytes tokens_total fencing fencing_token'.split())
    for table,column,actual in admin.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='horus'"):
        expected='text'
        if column.endswith('_canonical'): expected='bytea'
        elif column.endswith('_at') or column in ('at','expires','fetched','until','lease_until'): expected='timestamp with time zone'
        elif column in json_names and not (table=='transmissions' and column=='result'): expected='jsonb'
        elif column in integers and not (table=='schema_migrations' and column=='version'): expected='integer'
        elif column in bigints: expected='bigint'
        elif column in ('limit_amount','cost_usd','at_seconds'): expected='numeric'
        elif column in ('singleton','approved','publishable'): expected='boolean'
        elif column in ('instance_id','worker_instance_id') or (table=='worker_instances' and column=='id') or (table=='migration_runs' and column=='run_id'): expected='uuid'
        assert actual == expected, (table,column,actual,expected)


def test_schema_unique_contract(schema):
    admin,_=schema
    expected={
        'jobs':[('id',),('idempotency_key',),('workflow_id','workflow_revision','step_key')],
        'media':[('id',),('path',)],'workflows':[('id',),('idempotency_key',)],
        'job_attempts':[('id',),('job_id','number')],
        'artifacts':[('id',),('job_id',),('workflow_id','revision','stage')],
        'approvals':[('id',),('workflow_id','idempotency_key')],
        'cognitive_runs':[('id',),('idempotency_key',)],
        'authorizations':[('id',),('workflow_id','idempotency_key')],
        'scripts':[('id',),('workflow_id','version')],
        'script_receipts':[('scope','idempotency_key')],
        'productions':[('id',),('job_id',)],
        'production_receipts':[('scope','idempotency_key')],
        'license_reviews':[('seq',),('id',),('production_id','idempotency_key')],
        'human_checkpoints':[('seq',),('id',),('idempotency_key',)],
        'transmissions':[('seq',),('id',)],'radar_datasets':[('id','revision')],
        'database_identity':[('singleton',),('instance_id',)],
        'review_comment_receipts':[('production_id','scope','idempotency_key')],
        'publishing_receipts':[('production_id','scope','idempotency_key')],
        'notification_state':[('device_id','event_id')], 'preferences':[('device_id','key')],
        'imports':[('id',),('device_id','path')],
    }
    for table,keys in expected.items():
        actual=admin.execute("""SELECT ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(n,ord)
          JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n ORDER BY k.ord)
          FROM pg_index i WHERE i.indrelid=%s::regclass AND i.indisunique""",('horus.'+table,)).fetchall()
        assert {tuple(r[0]) for r in actual} == set(keys), table


def test_schema_all_foreign_keys(schema):
    admin,_=schema
    expected='''jobs.workflow_id=workflows.id jobs.depends_on=jobs.id jobs.worker_instance_id=worker_instances.id
    events.job_id=jobs.id workflows.current_artifact_id=artifacts.id job_attempts.job_id=jobs.id
    artifacts.workflow_id=workflows.id artifacts.job_id=jobs.id artifacts.attempt_id=job_attempts.id
    approvals.workflow_id=workflows.id approvals.artifact_id=artifacts.id workflow_events.workflow_id=workflows.id
    cognitive_runs.job_id=jobs.id cognitive_runs.attempt_id=job_attempts.id authorizations.workflow_id=workflows.id
    authorizations.job_id=jobs.id authorizations.input_artifact_id=artifacts.id authorizations.consumed_attempt_id=job_attempts.id
    scripts.workflow_id=workflows.id scripts.proposal_artifact_id=artifacts.id productions.script_id=scripts.id productions.job_id=jobs.id
    production_receipts.production_id=productions.id license_reviews.production_id=productions.id license_reviews.supersedes=license_reviews.id
    review_comments.production_id=productions.id review_comment_events.comment_id=review_comments.id
    review_comment_receipts.production_id=productions.id publishing_receipts.production_id=productions.id'''
    actual=admin.execute("""SELECT r.relname||'.'||a.attname||'='||f.relname||'.'||b.attname
      FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_class f ON f.oid=c.confrelid
      JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=c.conkey[1]
      JOIN pg_attribute b ON b.attrelid=f.oid AND b.attnum=c.confkey[1]
      WHERE c.connamespace='horus'::regnamespace AND c.contype='f'""").fetchall()
    assert {r[0] for r in actual} == set(expected.split())


def test_schema_migration_failure_atomic(schema,tmp_path,monkeypatch):
    import schema_support
    admin,_=schema
    path=tmp_path/'29990101000000_horus_failure.sql'
    path.write_text('SET ROLE horus_owner; CREATE TABLE horus.must_rollback(id text); SELECT 1/0; RESET ROLE;',encoding='utf8')
    monkeypatch.setattr(schema_support,'MIGRATIONS',schema_support.MIGRATIONS+[path])
    with pytest.raises(psycopg.errors.DivisionByZero): apply_chain(admin)
    assert admin.execute("SELECT to_regclass('horus.must_rollback')").fetchone() == (None,)
    assert admin.execute("SELECT count(*) FROM horus.schema_migrations WHERE version='29990101000000'").fetchone() == (0,)
    assert admin.execute('SELECT current_user').fetchone() == ('postgres',)


@pytest.mark.pg_strong
def test_schema_append_lock_precedes_identity(schema):
    import time
    admin,pool=schema
    started=threading.Event()
    second_pid=[]
    def append_second():
        with pool.connection() as second:
            second_pid.append(second.execute('SELECT pg_backend_pid()').fetchone()[0])
            started.set()
            return second.execute("INSERT INTO horus.transmissions(id,at,kind,stage,result) VALUES('second',now(),'probe','test','attempted') RETURNING seq").fetchone()[0]
    with pool.connection() as first:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with first.transaction():
                first.execute("INSERT INTO horus.transmissions(id,at,kind,stage,result) VALUES('first',now(),'probe','test','attempted')")
                future=executor.submit(append_second)
                assert started.wait(timeout=5)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    blocked=admin.execute('SELECT cardinality(pg_blocking_pids(%s))',(second_pid[0],)).fetchone()[0]
                    if blocked: break
                    time.sleep(.01)
                assert blocked == 1
                # A segunda conexão ainda não recebeu identity, nem pode commitar antes.
                assert admin.execute('SELECT last_value FROM horus.transmissions_seq_seq').fetchone() == (1,)
            assert future.result(timeout=5) == 2


def test_schema_catalog_totals(schema):
    admin,_=schema
    assert admin.execute("SELECT count(*) FROM pg_tables WHERE schemaname='horus'").fetchone() == (33,)
    assert admin.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relnamespace='horus'::regnamespace AND NOT t.tgisinternal").fetchone() == (18,)
    assert admin.execute("SELECT count(*) FROM pg_policies WHERE schemaname='horus'").fetchone() == (110,)


def test_schema_checkpoint_expired_clock_enforced(schema):
    admin,pool=schema
    checkpoint(admin)
    # O administrador prepara fixture expirada sem desabilitar trigger algum.
    columns=[c for c in COLUMNS['human_checkpoints'].split() if c not in ('seq','id')]
    values=["clock_timestamp()-interval '2 hours'" if c=='approved_at' else "clock_timestamp()-interval '1 hour'" if c=='expires_at' else c for c in columns]
    admin.execute('INSERT INTO horus.human_checkpoints(id,'+','.join(columns)+") SELECT 'expired',"+','.join(values)+' FROM horus.human_checkpoints')
    with pool.connection() as app:
        with pytest.raises(psycopg.errors.CheckViolation):
            # Mesmo um timestamp de consumo retroativo não evita o relógio real do servidor.
            app.execute("UPDATE horus.human_checkpoints SET consumed_at=approved_at+interval '1 minute',consumed_ref='r' WHERE id='expired'")


@pytest.mark.parametrize('table', ['review_comment_events','review_comment_receipts','imports'])
def test_schema_additional_append_only(schema,table):
    admin,pool=schema
    with pool.connection() as app:
        with app.transaction():
            graph(app)
            if table=='review_comment_events':
                app.execute("INSERT INTO horus.review_comments VALUES('comment','p',repeat('a',64),1,'text','Tester','human','open',now(),now())")
                app.execute("INSERT INTO horus.review_comment_events(comment_id,action,operator_id,created_at,data) VALUES('comment','created','Tester',now(),'{}')")
            elif table=='review_comment_receipts':
                app.execute("INSERT INTO horus.review_comment_receipts VALUES('p','create','k',repeat('a',64),'{}',%s)",(b'{}',))
            else:
                app.execute("INSERT INTO horus.imports(id,device_id,path,hash,manifest,created_at) VALUES('import','test','synthetic',repeat('a',64),'{}',now())")
    column='action' if table=='review_comment_events' else 'scope' if table=='review_comment_receipts' else 'path'
    for operation in ('UPDATE','DELETE'):
        statement=f'UPDATE horus.{table} SET {column}={column}' if operation=='UPDATE' else f'DELETE FROM horus.{table}'
        with pytest.raises(psycopg.errors.CheckViolation): admin.execute(statement)
        with pool.connection() as app:
            with pytest.raises(psycopg.errors.InsufficientPrivilege): app.execute(statement)


def test_schema_checksum_portable_newlines(tmp_path):
    windows=tmp_path/'windows.sql'
    linux=tmp_path/'linux.sql'
    windows.write_bytes(b'\xef\xbb\xbfSELECT 1;\r\nSELECT 2;\r\n')
    linux.write_bytes(b'SELECT 1;\nSELECT 2;\n')
    assert migration_checksum(windows) == migration_checksum(linux)
