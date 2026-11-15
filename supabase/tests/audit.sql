SELECT jsonb_build_object(
 'synthetic_accounts_remaining',(SELECT count(*) FROM auth.users WHERE id IN
   ('139b0000-0000-4000-8000-000000000001','139b0000-0000-4000-8000-000000000002')),
 'counts',jsonb_build_object('identities',(SELECT count(*) FROM horus_sync.identities),
   'workspaces',(SELECT count(*) FROM horus_sync.workspaces),'revisions',(SELECT count(*) FROM horus_sync.revisions),
   'receipts',(SELECT count(*) FROM horus_sync.receipts)),
 'roles',(SELECT jsonb_agg(jsonb_build_object('name',rolname,'super',rolsuper,'bypassrls',rolbypassrls,
   'createrole',rolcreaterole,'createdb',rolcreatedb,'replication',rolreplication,'login',rolcanlogin))
   FROM pg_roles WHERE rolname IN ('horus_sync_service','horus_sync_login')),
 'memberships',(SELECT jsonb_agg(jsonb_build_object('member',pg_get_userbyid(member),'role',pg_get_userbyid(roleid)))
   FROM pg_auth_members WHERE member IN (SELECT oid FROM pg_roles WHERE rolname IN ('horus_sync_service','horus_sync_login'))),
 'tables',(SELECT jsonb_agg(jsonb_build_object('name',c.relname,'rls',c.relrowsecurity,'force',c.relforcerowsecurity,
   'owner',pg_get_userbyid(c.relowner))) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='horus_sync' AND c.relkind='r')) AS isolation_audit;
