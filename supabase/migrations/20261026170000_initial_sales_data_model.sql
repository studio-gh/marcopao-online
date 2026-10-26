-- Marcopão Online: initial data model
-- This migration is reviewed into the repository first. Apply only after the
-- dedicated Supabase project is selected and provisioned.
begin;

create extension if not exists pgcrypto;

create table if not exists public.branches (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.report_imports (
  id uuid primary key default gen_random_uuid(),
  report_type text not null check (report_type in (
    'sale_day',
    'monthly_sales',
    'top_value',
    'top_quantity',
    'ticket_average',
    'sales_by_group',
    'products_by_group'
  )),
  branch_id uuid references public.branches(id) on delete restrict,
  branch_name_raw text,
  period_start date,
  period_end date,
  report_date date,
  source text not null default 'telegram_monitorbot',
  raw_text text not null,
  content_sha256 text not null,
  status text not null default 'imported'
    check (status in ('imported', 'validated', 'needs_review', 'rejected')),
  validation_notes jsonb not null default '[]'::jsonb,
  totals jsonb not null default '{}'::jsonb,
  imported_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (content_sha256)
);

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  canonical_name text not null unique,
  unit text,
  active boolean not null default true,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.product_aliases (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.products(id) on delete restrict,
  source_code text,
  source_name text not null,
  normalized_source_name text generated always as (lower(trim(source_name))) stored,
  source_system text not null default 'bellabyte',
  match_status text not null default 'confirmed'
    check (match_status in ('confirmed', 'suggested', 'rejected')),
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_aliases_source_identity_unique
    unique (source_system, source_code, normalized_source_name)
);

create table if not exists public.group_sales (
  id uuid primary key default gen_random_uuid(),
  report_import_id uuid not null references public.report_imports(id) on delete cascade,
  group_name text not null,
  revenue numeric(14,2) not null default 0 check (revenue >= 0),
  quantity numeric(14,3),
  average_sale_price numeric(14,4),
  created_at timestamptz not null default now(),
  unique (report_import_id, group_name)
);

create table if not exists public.product_sales (
  id uuid primary key default gen_random_uuid(),
  report_import_id uuid not null references public.report_imports(id) on delete cascade,
  group_name text,
  product_id uuid references public.products(id) on delete restrict,
  source_code text,
  source_name text not null,
  quantity numeric(14,3),
  unit text,
  revenue numeric(14,2) not null default 0 check (revenue >= 0),
  unit_price numeric(14,4),
  created_at timestamptz not null default now()
);

create index if not exists report_imports_type_period_idx
  on public.report_imports (report_type, period_start, period_end);
create index if not exists report_imports_branch_idx
  on public.report_imports (branch_id, imported_at desc);
create index if not exists group_sales_import_group_idx
  on public.group_sales (report_import_id, group_name);
create index if not exists product_sales_import_group_idx
  on public.product_sales (report_import_id, group_name);
create index if not exists product_sales_product_idx
  on public.product_sales (product_id);
create index if not exists product_aliases_normalized_name_idx
  on public.product_aliases (source_system, normalized_source_name);
create index if not exists product_aliases_source_code_idx
  on public.product_aliases (source_system, source_code);

-- Default deny: the browser must not read/write these tables anonymously.
-- Access will be provided through authenticated application flows or a server-side
-- Edge Function using a server-only key, never by exposing a service-role key in HTML.
alter table public.branches enable row level security;
alter table public.report_imports enable row level security;
alter table public.products enable row level security;
alter table public.product_aliases enable row level security;
alter table public.group_sales enable row level security;
alter table public.product_sales enable row level security;

commit;
