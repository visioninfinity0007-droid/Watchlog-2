# WatchLog Platform Admin Bootstrap

This runbook creates or promotes the first WatchLog platform administrator without weakening tenant isolation.

## Preconditions

- Migrations through `0029_platform_admin.sql` are applied to the WatchLog Supabase project.
- The operator has the migration-only Postgres credentials from the production `.env`.
- The email being promoted belongs to the intended administrator.

The customer roles (`owner`, `admin`, `viewer`) and platform roles (`platform_owner`, `platform_admin`, `platform_support`) are separate. A customer owner must never become a platform administrator implicitly.

## Recommended path: promote an existing WatchLog login

1. Sign up or sign in once through the normal WatchLog portal so the user exists in Supabase Auth.
2. From the protected operations environment, export the production database variables:

```bash
export SUPABASE_DB_HOST='...'
export SUPABASE_DB_PORT='5432'
export SUPABASE_DB_USER='...'
export SUPABASE_DB_PASSWORD='...'
export SUPABASE_DB_NAME='postgres'
```

3. Run:

```bash
python -m pip install 'psycopg[binary]'
python tools/bootstrap_platform_admin.py --email '<ADMIN_EMAIL>' --role platform_owner
```

The script is idempotent. Re-running it for the same email updates the platform role rather than creating duplicate rows.

## Optional path: create the Auth login and promote it

Only use this when the environment already carries a valid Supabase secret/service key. Never commit the key or password.

```bash
export SUPABASE_URL='https://<project-ref>.supabase.co'
export SUPABASE_SECRET_KEY='...'
export PLATFORM_ADMIN_PASSWORD='a-long-random-password'
python tools/bootstrap_platform_admin.py --email '<ADMIN_EMAIL>' --role platform_owner --create-user
```

The password must be at least 12 characters. The tool never prints it.

## Login verification

After promotion:

1. Open the normal WatchLog login page.
2. Sign in with the promoted account.
3. The root router should send the account to `/admin/`.
4. Confirm the Platform Overview loads.
5. Confirm `Tenants`, `Operations`, `Billing`, `Audit`, and `Admins` are reachable as appropriate for the assigned platform role.
6. If the account also belongs to a tenant, confirm the `Customer Portal` switch works and does not alter the platform role.

## Authorization verification

Run the live gate using a normal tenant owner account, not the platform owner:

```bash
python prototype/tests/test_platform_admin_authz.py
```

Expected result: a normal tenant owner can call the harmless `wl_platform_me` probe and receive `null`, but all cross-tenant platform RPCs are denied.

## Recovery

If the only platform owner loses access, use the database owner connection to assign a different existing Auth user:

```sql
insert into public.platform_admins(user_id, role)
select id, 'platform_owner'
from auth.users
where lower(email)=lower('<RECOVERY_EMAIL>')
on conflict (user_id) do update set role='platform_owner';
```

Do not add a public or authenticated bootstrap RPC. The first-owner bootstrap intentionally remains an operations-only database action.

## Security rules

- Never expose `platform_admins` directly to browser roles.
- Never grant tenant deletion to the admin UI.
- Never add silent customer impersonation.
- Every cross-tenant write requires an audit reason.
- Paid subscription overrides remain `platform_owner` only.
- Rotate credentials immediately if a database or Coolify secret is pasted into chat, tickets, or logs.
