-- Marcopão Online: latest Telegram snapshot wins within each report scope.
-- Keep prior snapshots in report_imports for audit/history; mark them superseded.
alter table public.report_imports
  drop constraint if exists report_imports_status_check;

alter table public.report_imports
  add constraint report_imports_status_check
  check (status = any (array['imported','validated','needs_review','rejected','superseded']::text[]));

with ranked as (
  select id,
         row_number() over (
           partition by report_type,
                        coalesce(branch_id::text, upper(trim(coalesce(branch_name_raw,'')))),
                        period_start, period_end, report_date
           order by (source = 'telegram_monitorbot') desc,
                    imported_at desc,
                    created_at desc,
                    id desc
         ) as rn
  from public.report_imports
  where status <> 'rejected'
)
update public.report_imports as r
set status = case when ranked.rn = 1 then 'validated' else 'superseded' end
from ranked
where r.id = ranked.id
  and r.status is distinct from case when ranked.rn = 1 then 'validated' else 'superseded' end;
