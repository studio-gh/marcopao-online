-- Safe browser read model for daily sales.
-- Daily imports are stored separately from the general report summaries.
-- Expose only the fields required by the dashboard and strip Telegram raw text
-- and message identifiers from nested card details before browser access.

begin;

create or replace view public.dashboard_daily_report_summaries
with (security_barrier = true)
as
select
  report_type,
  branch_name_raw,
  report_date,
  period_start,
  period_end,
  case
    when jsonb_typeof(totals) = 'object' and totals ? 'card_details'
      then jsonb_set(
        totals,
        '{card_details}',
        (totals->'card_details') - array['raw_text','message_id','message_date','content_sha256']
      )
    when jsonb_typeof(totals) = 'object' then totals
    else '{}'::jsonb
  end as totals,
  status,
  source,
  imported_at
from public.dashboard_daily_reports
where report_type = 'sale_day';

revoke all on public.dashboard_daily_report_summaries from public;
grant select on public.dashboard_daily_report_summaries to anon, authenticated;

commit;

notify pgrst, 'reload schema';
