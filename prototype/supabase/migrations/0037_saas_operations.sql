-- =====================================================================
-- 0037 - SaaS operations: commercial documents, support notes and mode
--
-- This migration extends the platform-admin control plane into an operating
-- console without granting browser roles direct cross-tenant table access.
-- Every platform mutation is performed through an audited SECURITY DEFINER
-- RPC with a pinned search_path.
--
-- Support Mode is intentionally NOT auth impersonation. The platform user
-- keeps their own identity and an expiring, reason-bound support session is
-- recorded for the selected customer.
-- =====================================================================

create sequence if not exists public.wl_invoice_number_seq;
create sequence if not exists public.wl_contract_number_seq;

create table if not exists public.customer_billing_profiles (
  tenant_id            uuid primary key references public.tenants(id) on delete cascade,
  legal_name           text,
  billing_email        text,
  billing_address      text,
  tax_id               text,
  currency             text not null default 'PKR',
  payment_terms_days   integer not null default 14 check (payment_terms_days between 0 and 365),
  updated_at           timestamptz not null default now(),
  updated_by           uuid references auth.users(id) on delete set null
);

create table if not exists public.platform_support_notes (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  note                 text not null check (length(btrim(note)) >= 2),
  created_by           uuid references auth.users(id) on delete set null,
  created_at           timestamptz not null default now()
);
create index if not exists platform_support_notes_tenant_idx
  on public.platform_support_notes(tenant_id, created_at desc);

create table if not exists public.platform_support_sessions (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  actor_user_id        uuid not null references auth.users(id) on delete cascade,
  actor_role           text not null,
  reason               text not null,
  started_at           timestamptz not null default now(),
  expires_at           timestamptz not null default (now() + interval '45 minutes'),
  ended_at             timestamptz,
  check (expires_at > started_at)
);
create index if not exists platform_support_sessions_actor_idx
  on public.platform_support_sessions(actor_user_id, started_at desc);
create index if not exists platform_support_sessions_tenant_idx
  on public.platform_support_sessions(tenant_id, started_at desc);

create table if not exists public.commercial_invoices (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  invoice_number       text not null unique,
  status               text not null default 'draft' check (status in ('draft','sent','paid','void','overdue')),
  issue_date           date not null default current_date,
  due_date             date not null,
  currency             text not null default 'PKR',
  subtotal_minor       bigint not null default 0 check (subtotal_minor >= 0),
  tax_minor            bigint not null default 0 check (tax_minor >= 0),
  total_minor          bigint not null default 0 check (total_minor >= 0),
  notes                text,
  created_by           uuid references auth.users(id) on delete set null,
  created_at           timestamptz not null default now(),
  sent_at              timestamptz,
  paid_at              timestamptz
);
create index if not exists commercial_invoices_tenant_idx
  on public.commercial_invoices(tenant_id, created_at desc);

create table if not exists public.commercial_invoice_items (
  id                   bigint generated always as identity primary key,
  invoice_id           bigint not null references public.commercial_invoices(id) on delete cascade,
  description          text not null check (length(btrim(description)) >= 2),
  quantity             numeric(12,2) not null default 1 check (quantity > 0),
  unit_amount_minor    bigint not null check (unit_amount_minor >= 0),
  amount_minor         bigint not null check (amount_minor >= 0)
);
create index if not exists commercial_invoice_items_invoice_idx
  on public.commercial_invoice_items(invoice_id, id);

create table if not exists public.commercial_contracts (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  contract_number      text not null unique,
  title                text not null check (length(btrim(title)) >= 2),
  status               text not null default 'draft' check (status in ('draft','sent','signed','expired','terminated')),
  starts_on            date,
  ends_on              date,
  currency             text not null default 'PKR',
  value_minor          bigint check (value_minor is null or value_minor >= 0),
  terms                text,
  document_url         text,
  created_by           uuid references auth.users(id) on delete set null,
  created_at           timestamptz not null default now(),
  sent_at              timestamptz,
  signed_at            timestamptz,
  check (ends_on is null or starts_on is null or ends_on >= starts_on)
);
create index if not exists commercial_contracts_tenant_idx
  on public.commercial_contracts(tenant_id, created_at desc);

create table if not exists public.commercial_manual_payments (
  id                   bigint generated always as identity primary key,
  tenant_id            uuid not null references public.tenants(id) on delete cascade,
  invoice_id           bigint references public.commercial_invoices(id) on delete set null,
  amount_minor         bigint not null check (amount_minor > 0),
  currency             text not null default 'PKR',
  method               text not null,
  reference            text,
  received_at          timestamptz not null default now(),
  note                 text,
  created_by           uuid references auth.users(id) on delete set null,
  created_at           timestamptz not null default now()
);
create index if not exists commercial_manual_payments_tenant_idx
  on public.commercial_manual_payments(tenant_id, received_at desc);

alter table public.customer_billing_profiles enable row level security;
alter table public.platform_support_notes enable row level security;
alter table public.platform_support_sessions enable row level security;
alter table public.commercial_invoices enable row level security;
alter table public.commercial_invoice_items enable row level security;
alter table public.commercial_contracts enable row level security;
alter table public.commercial_manual_payments enable row level security;

revoke all on public.customer_billing_profiles from anon, authenticated;
revoke all on public.platform_support_notes from anon, authenticated;
revoke all on public.platform_support_sessions from anon, authenticated;
revoke all on public.commercial_invoices from anon, authenticated;
revoke all on public.commercial_invoice_items from anon, authenticated;
revoke all on public.commercial_contracts from anon, authenticated;
revoke all on public.commercial_manual_payments from anon, authenticated;
revoke all on sequence public.wl_invoice_number_seq from anon, authenticated;
revoke all on sequence public.wl_contract_number_seq from anon, authenticated;

-- ---------------------------------------------------------------------
-- Shared validation helpers remain private to database/internal callers.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_assert_tenant(p_tenant_id uuid)
returns void
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not exists(select 1 from tenants where id=p_tenant_id) then
    raise exception 'customer not found' using errcode='22023';
  end if;
end $$;
revoke all on function public.wl_platform_assert_tenant(uuid) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- Customer commercial/support read model.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_commercial(p_tenant_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  return jsonb_build_object(
    'role', v_role,
    'billing_profile', (select to_jsonb(b) - 'updated_by' from customer_billing_profiles b where b.tenant_id=p_tenant_id),
    'invoices', coalesce((select jsonb_agg(jsonb_build_object(
      'id',i.id,'invoice_number',i.invoice_number,'status',i.status,'issue_date',i.issue_date,
      'due_date',i.due_date,'currency',i.currency,'subtotal_minor',i.subtotal_minor,
      'tax_minor',i.tax_minor,'total_minor',i.total_minor,'notes',i.notes,
      'created_at',i.created_at,'sent_at',i.sent_at,'paid_at',i.paid_at
    ) order by i.created_at desc) from commercial_invoices i where i.tenant_id=p_tenant_id),'[]'::jsonb),
    'contracts', coalesce((select jsonb_agg(jsonb_build_object(
      'id',c.id,'contract_number',c.contract_number,'title',c.title,'status',c.status,
      'starts_on',c.starts_on,'ends_on',c.ends_on,'currency',c.currency,'value_minor',c.value_minor,
      'document_url',c.document_url,'created_at',c.created_at,'sent_at',c.sent_at,'signed_at',c.signed_at
    ) order by c.created_at desc) from commercial_contracts c where c.tenant_id=p_tenant_id),'[]'::jsonb),
    'payments', coalesce((select jsonb_agg(jsonb_build_object(
      'id',p.id,'invoice_id',p.invoice_id,'amount_minor',p.amount_minor,'currency',p.currency,
      'method',p.method,'reference',p.reference,'received_at',p.received_at,'note',p.note
    ) order by p.received_at desc) from commercial_manual_payments p where p.tenant_id=p_tenant_id limit 100),'[]'::jsonb),
    'support_notes', coalesce((select jsonb_agg(jsonb_build_object(
      'id',n.id,'note',n.note,'created_at',n.created_at,
      'created_by',(select u.email from auth.users u where u.id=n.created_by)
    ) order by n.created_at desc) from platform_support_notes n where n.tenant_id=p_tenant_id limit 100),'[]'::jsonb)
  );
end $$;
revoke all on function public.wl_platform_commercial(uuid) from public, anon;
grant execute on function public.wl_platform_commercial(uuid) to authenticated;

-- Owner-only customer-facing document history. Internal support notes and
-- platform session data are never returned here.
create or replace function public.wl_customer_documents()
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_tenant uuid := wl_my_tenant(); v_role text := wl_my_role();
begin
  if v_tenant is null then raise exception 'customer account required' using errcode='42501'; end if;
  if v_role <> 'owner' then raise exception 'owner access required' using errcode='42501'; end if;
  return jsonb_build_object(
    'billing_profile',(select to_jsonb(b) - 'updated_by' from customer_billing_profiles b where b.tenant_id=v_tenant),
    'invoices',coalesce((select jsonb_agg(jsonb_build_object(
      'id',i.id,'invoice_number',i.invoice_number,'status',i.status,'issue_date',i.issue_date,
      'due_date',i.due_date,'currency',i.currency,'total_minor',i.total_minor,'paid_at',i.paid_at
    ) order by i.created_at desc) from commercial_invoices i where i.tenant_id=v_tenant),'[]'::jsonb),
    'contracts',coalesce((select jsonb_agg(jsonb_build_object(
      'id',c.id,'contract_number',c.contract_number,'title',c.title,'status',c.status,
      'starts_on',c.starts_on,'ends_on',c.ends_on,'currency',c.currency,'value_minor',c.value_minor,
      'document_url',c.document_url,'signed_at',c.signed_at
    ) order by c.created_at desc) from commercial_contracts c where c.tenant_id=v_tenant),'[]'::jsonb)
  );
end $$;
revoke all on function public.wl_customer_documents() from public, anon;
grant execute on function public.wl_customer_documents() to authenticated;

create or replace function public.wl_platform_invoice(p_invoice_id bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']); i commercial_invoices;
begin
  select * into i from commercial_invoices where id=p_invoice_id;
  if i.id is null then raise exception 'invoice not found' using errcode='22023'; end if;
  return jsonb_build_object(
    'role',v_role,
    'tenant',(select jsonb_build_object('id',t.id,'name',t.name) from tenants t where t.id=i.tenant_id),
    'billing_profile',(select to_jsonb(b) - 'updated_by' from customer_billing_profiles b where b.tenant_id=i.tenant_id),
    'invoice',to_jsonb(i) - 'created_by',
    'items',coalesce((select jsonb_agg(to_jsonb(x) order by x.id) from commercial_invoice_items x where x.invoice_id=i.id),'[]'::jsonb),
    'payments',coalesce((select jsonb_agg(to_jsonb(p) - 'created_by' order by p.received_at) from commercial_manual_payments p where p.invoice_id=i.id),'[]'::jsonb)
  );
end $$;
revoke all on function public.wl_platform_invoice(bigint) from public, anon;
grant execute on function public.wl_platform_invoice(bigint) to authenticated;

create or replace function public.wl_platform_contract(p_contract_id bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']); c commercial_contracts;
begin
  select * into c from commercial_contracts where id=p_contract_id;
  if c.id is null then raise exception 'contract not found' using errcode='22023'; end if;
  return jsonb_build_object(
    'role',v_role,
    'tenant',(select jsonb_build_object('id',t.id,'name',t.name) from tenants t where t.id=c.tenant_id),
    'billing_profile',(select to_jsonb(b) - 'updated_by' from customer_billing_profiles b where b.tenant_id=c.tenant_id),
    'contract',to_jsonb(c) - 'created_by'
  );
end $$;
revoke all on function public.wl_platform_contract(bigint) from public, anon;
grant execute on function public.wl_platform_contract(bigint) to authenticated;

-- ---------------------------------------------------------------------
-- Billing profile and internal support notes.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_set_billing_profile(
  p_tenant_id uuid,
  p_legal_name text,
  p_billing_email text,
  p_billing_address text,
  p_tax_id text,
  p_currency text,
  p_payment_terms_days integer,
  p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); v_before jsonb; v_after jsonb;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if p_payment_terms_days is null or p_payment_terms_days < 0 or p_payment_terms_days > 365 then
    raise exception 'payment terms must be between 0 and 365 days';
  end if;
  select to_jsonb(b) into v_before from customer_billing_profiles b where b.tenant_id=p_tenant_id;
  insert into customer_billing_profiles(tenant_id,legal_name,billing_email,billing_address,tax_id,currency,payment_terms_days,updated_at,updated_by)
  values(p_tenant_id,nullif(btrim(p_legal_name),''),nullif(btrim(p_billing_email),''),nullif(btrim(p_billing_address),''),nullif(btrim(p_tax_id),''),upper(coalesce(nullif(btrim(p_currency),''),'PKR')),p_payment_terms_days,now(),auth.uid())
  on conflict (tenant_id) do update set
    legal_name=excluded.legal_name,billing_email=excluded.billing_email,billing_address=excluded.billing_address,
    tax_id=excluded.tax_id,currency=excluded.currency,payment_terms_days=excluded.payment_terms_days,
    updated_at=now(),updated_by=auth.uid();
  select to_jsonb(b) into v_after from customer_billing_profiles b where b.tenant_id=p_tenant_id;
  perform wl_platform_write_audit(p_tenant_id,'billing_profile_updated',p_reason,v_before,v_after);
  return v_after - 'updated_by';
end $$;
revoke all on function public.wl_platform_set_billing_profile(uuid,text,text,text,text,text,integer,text) from public, anon;
grant execute on function public.wl_platform_set_billing_profile(uuid,text,text,text,text,text,integer,text) to authenticated;

create or replace function public.wl_platform_add_support_note(p_tenant_id uuid, p_note text, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']); v_id bigint;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if length(btrim(coalesce(p_note,''))) < 2 then raise exception 'support note is required'; end if;
  insert into platform_support_notes(tenant_id,note,created_by) values(p_tenant_id,btrim(p_note),auth.uid()) returning id into v_id;
  perform wl_platform_write_audit(p_tenant_id,'support_note_added',p_reason,null,jsonb_build_object('note_id',v_id));
  return jsonb_build_object('id',v_id,'ok',true);
end $$;
revoke all on function public.wl_platform_add_support_note(uuid,text,text) from public, anon;
grant execute on function public.wl_platform_add_support_note(uuid,text,text) to authenticated;

-- ---------------------------------------------------------------------
-- Safe support-mode sessions. These preserve the platform actor identity.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_start_support_mode(p_tenant_id uuid, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']); v_id bigint; v_exp timestamptz;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if length(btrim(coalesce(p_reason,''))) < 4 then raise exception 'support reason is required'; end if;
  update platform_support_sessions set ended_at=now()
    where actor_user_id=auth.uid() and ended_at is null and expires_at>now();
  insert into platform_support_sessions(tenant_id,actor_user_id,actor_role,reason)
  values(p_tenant_id,auth.uid(),v_role,btrim(p_reason)) returning id,expires_at into v_id,v_exp;
  perform wl_platform_write_audit(p_tenant_id,'support_mode_started',p_reason,null,jsonb_build_object('support_session_id',v_id,'expires_at',v_exp));
  return jsonb_build_object('id',v_id,'tenant_id',p_tenant_id,'expires_at',v_exp,'role',v_role);
end $$;
revoke all on function public.wl_platform_start_support_mode(uuid,text) from public, anon;
grant execute on function public.wl_platform_start_support_mode(uuid,text) to authenticated;

create or replace function public.wl_platform_support_session(p_session_id bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare s platform_support_sessions; v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  select * into s from platform_support_sessions where id=p_session_id and actor_user_id=auth.uid();
  if s.id is null or s.ended_at is not null or s.expires_at<=now() then
    raise exception 'support session is unavailable or expired' using errcode='42501';
  end if;
  return jsonb_build_object(
    'id',s.id,'tenant_id',s.tenant_id,'reason',s.reason,'started_at',s.started_at,'expires_at',s.expires_at,
    'role',v_role,'customer',wl_platform_tenant(s.tenant_id),'commercial',wl_platform_commercial(s.tenant_id)
  );
end $$;
revoke all on function public.wl_platform_support_session(bigint) from public, anon;
grant execute on function public.wl_platform_support_session(bigint) to authenticated;

create or replace function public.wl_platform_end_support_mode(p_session_id bigint, p_reason text default 'support session completed')
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare s platform_support_sessions; v_role text := wl_platform_require(array['platform_owner','platform_admin','platform_support']);
begin
  select * into s from platform_support_sessions where id=p_session_id and actor_user_id=auth.uid();
  if s.id is null then raise exception 'support session not found' using errcode='22023'; end if;
  if s.ended_at is null then update platform_support_sessions set ended_at=now() where id=s.id; end if;
  perform wl_platform_write_audit(s.tenant_id,'support_mode_ended',coalesce(nullif(btrim(p_reason),''),'support session completed'),jsonb_build_object('support_session_id',s.id),jsonb_build_object('ended',true));
  return jsonb_build_object('ok',true,'role',v_role);
end $$;
revoke all on function public.wl_platform_end_support_mode(bigint,text) from public, anon;
grant execute on function public.wl_platform_end_support_mode(bigint,text) to authenticated;

-- ---------------------------------------------------------------------
-- Invoice lifecycle.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_create_invoice(
  p_tenant_id uuid,
  p_due_date date,
  p_currency text,
  p_items jsonb,
  p_tax_minor bigint default 0,
  p_notes text default null,
  p_reason text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); v_id bigint; v_number text;
        v_item jsonb; v_qty numeric(12,2); v_unit bigint; v_amount bigint; v_subtotal bigint := 0; v_total bigint;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if p_due_date is null or p_due_date < current_date then raise exception 'invoice due date cannot be before today'; end if;
  if jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items)=0 then raise exception 'at least one invoice item is required'; end if;
  if coalesce(p_tax_minor,0) < 0 then raise exception 'tax amount cannot be negative'; end if;
  if length(btrim(coalesce(p_reason,''))) < 4 then raise exception 'an audit reason is required'; end if;
  v_number := 'WL-' || to_char(current_date,'YYYY') || '-' || lpad(nextval('wl_invoice_number_seq')::text,6,'0');
  insert into commercial_invoices(tenant_id,invoice_number,due_date,currency,tax_minor,notes,created_by)
  values(p_tenant_id,v_number,p_due_date,upper(coalesce(nullif(btrim(p_currency),''),'PKR')),coalesce(p_tax_minor,0),nullif(btrim(p_notes),''),auth.uid())
  returning id into v_id;
  for v_item in select value from jsonb_array_elements(p_items)
  loop
    if length(btrim(coalesce(v_item->>'description',''))) < 2 then raise exception 'invoice item description is required'; end if;
    v_qty := coalesce((v_item->>'quantity')::numeric,1);
    v_unit := coalesce((v_item->>'unit_amount_minor')::bigint,-1);
    if v_qty<=0 or v_unit<0 then raise exception 'invalid invoice item quantity or amount'; end if;
    v_amount := round(v_qty * v_unit)::bigint;
    insert into commercial_invoice_items(invoice_id,description,quantity,unit_amount_minor,amount_minor)
    values(v_id,btrim(v_item->>'description'),v_qty,v_unit,v_amount);
    v_subtotal := v_subtotal + v_amount;
  end loop;
  v_total := v_subtotal + coalesce(p_tax_minor,0);
  update commercial_invoices set subtotal_minor=v_subtotal,total_minor=v_total where id=v_id;
  perform wl_platform_write_audit(p_tenant_id,'invoice_created',p_reason,null,jsonb_build_object('invoice_id',v_id,'invoice_number',v_number,'total_minor',v_total));
  return wl_platform_invoice(v_id);
exception when others then
  if v_id is not null then delete from commercial_invoices where id=v_id; end if;
  raise;
end $$;
revoke all on function public.wl_platform_create_invoice(uuid,date,text,jsonb,bigint,text,text) from public, anon;
grant execute on function public.wl_platform_create_invoice(uuid,date,text,jsonb,bigint,text,text) to authenticated;

create or replace function public.wl_platform_set_invoice_status(p_invoice_id bigint, p_status text, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); i commercial_invoices; v_before jsonb;
begin
  select * into i from commercial_invoices where id=p_invoice_id;
  if i.id is null then raise exception 'invoice not found' using errcode='22023'; end if;
  if p_status not in ('draft','sent','paid','void','overdue') then raise exception 'invalid invoice status'; end if;
  v_before := to_jsonb(i);
  update commercial_invoices set status=p_status,
    sent_at=case when p_status='sent' then coalesce(sent_at,now()) else sent_at end,
    paid_at=case when p_status='paid' then coalesce(paid_at,now()) when p_status<>'paid' then null else paid_at end
    where id=i.id;
  perform wl_platform_write_audit(i.tenant_id,'invoice_status_changed',p_reason,v_before,(select to_jsonb(x) from commercial_invoices x where x.id=i.id));
  return wl_platform_invoice(i.id);
end $$;
revoke all on function public.wl_platform_set_invoice_status(bigint,text,text) from public, anon;
grant execute on function public.wl_platform_set_invoice_status(bigint,text,text) to authenticated;

create or replace function public.wl_platform_record_payment(
  p_tenant_id uuid,
  p_invoice_id bigint,
  p_amount_minor bigint,
  p_currency text,
  p_method text,
  p_reference text,
  p_received_at timestamptz,
  p_note text,
  p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); i commercial_invoices; v_id bigint; v_paid bigint;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if p_amount_minor is null or p_amount_minor<=0 then raise exception 'payment amount must be positive'; end if;
  if length(btrim(coalesce(p_method,'')))<2 then raise exception 'payment method is required'; end if;
  if p_invoice_id is not null then
    select * into i from commercial_invoices where id=p_invoice_id and tenant_id=p_tenant_id;
    if i.id is null then raise exception 'invoice does not belong to this customer'; end if;
  end if;
  insert into commercial_manual_payments(tenant_id,invoice_id,amount_minor,currency,method,reference,received_at,note,created_by)
  values(p_tenant_id,p_invoice_id,p_amount_minor,upper(coalesce(nullif(btrim(p_currency),''),'PKR')),btrim(p_method),nullif(btrim(p_reference),''),coalesce(p_received_at,now()),nullif(btrim(p_note),''),auth.uid()) returning id into v_id;
  if p_invoice_id is not null then
    select coalesce(sum(amount_minor),0) into v_paid from commercial_manual_payments where invoice_id=p_invoice_id;
    if v_paid >= i.total_minor then update commercial_invoices set status='paid',paid_at=coalesce(paid_at,now()) where id=p_invoice_id and status<>'void'; end if;
  end if;
  perform wl_platform_write_audit(p_tenant_id,'manual_payment_recorded',p_reason,null,jsonb_build_object('payment_id',v_id,'invoice_id',p_invoice_id,'amount_minor',p_amount_minor));
  return jsonb_build_object('ok',true,'payment_id',v_id,'invoice',case when p_invoice_id is null then null else wl_platform_invoice(p_invoice_id) end);
end $$;
revoke all on function public.wl_platform_record_payment(uuid,bigint,bigint,text,text,text,timestamptz,text,text) from public, anon;
grant execute on function public.wl_platform_record_payment(uuid,bigint,bigint,text,text,text,timestamptz,text,text) to authenticated;

-- ---------------------------------------------------------------------
-- Contract lifecycle.
-- ---------------------------------------------------------------------
create or replace function public.wl_platform_create_contract(
  p_tenant_id uuid,
  p_title text,
  p_starts_on date,
  p_ends_on date,
  p_value_minor bigint,
  p_currency text,
  p_terms text,
  p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); v_id bigint; v_number text;
begin
  perform wl_platform_assert_tenant(p_tenant_id);
  if length(btrim(coalesce(p_title,'')))<2 then raise exception 'contract title is required'; end if;
  if p_ends_on is not null and p_starts_on is not null and p_ends_on<p_starts_on then raise exception 'contract end date cannot precede start date'; end if;
  if p_value_minor is not null and p_value_minor<0 then raise exception 'contract value cannot be negative'; end if;
  v_number := 'WLC-' || to_char(current_date,'YYYY') || '-' || lpad(nextval('wl_contract_number_seq')::text,6,'0');
  insert into commercial_contracts(tenant_id,contract_number,title,starts_on,ends_on,value_minor,currency,terms,created_by)
  values(p_tenant_id,v_number,btrim(p_title),p_starts_on,p_ends_on,p_value_minor,upper(coalesce(nullif(btrim(p_currency),''),'PKR')),nullif(btrim(p_terms),''),auth.uid()) returning id into v_id;
  perform wl_platform_write_audit(p_tenant_id,'contract_created',p_reason,null,jsonb_build_object('contract_id',v_id,'contract_number',v_number));
  return wl_platform_contract(v_id);
end $$;
revoke all on function public.wl_platform_create_contract(uuid,text,date,date,bigint,text,text,text) from public, anon;
grant execute on function public.wl_platform_create_contract(uuid,text,date,date,bigint,text,text,text) to authenticated;

create or replace function public.wl_platform_set_contract_status(
  p_contract_id bigint,
  p_status text,
  p_document_url text,
  p_reason text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare v_role text := wl_platform_require(array['platform_owner','platform_admin']); c commercial_contracts; v_before jsonb;
begin
  select * into c from commercial_contracts where id=p_contract_id;
  if c.id is null then raise exception 'contract not found' using errcode='22023'; end if;
  if p_status not in ('draft','sent','signed','expired','terminated') then raise exception 'invalid contract status'; end if;
  v_before := to_jsonb(c);
  update commercial_contracts set status=p_status,document_url=coalesce(nullif(btrim(p_document_url),''),document_url),
    sent_at=case when p_status='sent' then coalesce(sent_at,now()) else sent_at end,
    signed_at=case when p_status='signed' then coalesce(signed_at,now()) else signed_at end
    where id=c.id;
  perform wl_platform_write_audit(c.tenant_id,'contract_status_changed',p_reason,v_before,(select to_jsonb(x) from commercial_contracts x where x.id=c.id));
  return wl_platform_contract(c.id);
end $$;
revoke all on function public.wl_platform_set_contract_status(bigint,text,text,text) from public, anon;
grant execute on function public.wl_platform_set_contract_status(bigint,text,text,text) to authenticated;
