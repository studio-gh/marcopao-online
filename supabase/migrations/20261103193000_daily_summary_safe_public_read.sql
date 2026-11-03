create or replace view public.dashboard_daily_report_summaries
with (security_barrier = true, security_invoker = false)
as
select
  report_type,
  branch_name_raw,
  report_date,
  period_start,
  period_end,
  case
    when jsonb_typeof(totals) = 'object' and totals ? 'card_details' then
      jsonb_set(
        totals,
        '{card_details}',
        (totals -> 'card_details') - array['raw_text', 'message_id', 'message_date', 'content_sha256']
      )
    when jsonb_typeof(totals) = 'object' then totals
    else '{}'::jsonb
  end as totals,
  status,
  source,
  imported_at
from public.report_imports
where report_type = 'sale_day'
  and status = any (array['needs_review', 'validated']);
