# Marcopão report automation: current state and rollout

## Verified

- The Bellabyte bot has successfully returned the October 2026 monthly-sales report for `MARCOPAO LANCHONETE E PANIFICACAO LTDA`.
- The main report flow is: select branch, choose report, choose year/month where applicable, then answer `Não` to the optional card details prompt.
- The report's raw text can be preserved in Supabase with a SHA-256 fingerprint. The collector now stores records with status `needs_review`; it does not mark them validated automatically.
- The public dashboard has a dedicated daily comparison panel and no longer substitutes its previous hard-coded example sales totals. Its daily screen starts as the default view.
- The dashboard is a static GitHub Pages site and is not yet reading reports live from Supabase. It must not be given public read access to confidential sales data.

## Current collector

Workflow: [Marcopão report collector](https://github.com/studio-gh/marcopao-online/actions/workflows/monitorbot-monthly-probe.yml)

- Manual options: `daily_yesterday` and `monthly_october`.
- Schedule: daily at 03:10 UTC (00:10 in America/Sao_Paulo) to request the previous day's sales.
- Navigation is state-aware: it uses the bot's documented `0` reset for an unexpected state, then reselects the branch and confirms the menu before proceeding.
- Bot report contents are redacted from public GitHub Actions logs.
- The report is stored in `public.report_imports` with `status='needs_review'`, the raw text, parsed totals, a content hash, and validation warnings. No row is promoted to `validated` by the collector.

## One-time secret needed

In GitHub repository **Settings → Secrets and variables → Actions**, add:

- Name: `SUPABASE_SERVICE_ROLE_KEY`
- Value: the project's Supabase **service_role** secret/JWT key, not a publishable/anon key.

This privileged key must remain in GitHub Secrets only. Never commit it, paste it into chat, or print it in workflow logs. The workflow stops before sending any Telegram messages if this secret is missing.

## Immediate test

1. Add the secret above.
2. Open the collector workflow and run it manually with `daily_yesterday`.
3. Review the summary logs only. The raw report body is redacted from public logs.
4. Verify the new `report_imports` row in Supabase and its `needs_review` status before using the figures.
5. A genuine day-over-day comparison requires two consecutive daily report records. The first collection alone cannot produce a real comparison.

## Validation rules

- Keep amounts from the bot as received. Never silently reconcile totals.
- Warn if payment-method totals disagree with the reported total.
- Keep cancellation, unfinished-sale and completed-sale figures in separate fields.
- A current-month query must not be labelled a final closed month without evidence.
- Use idempotency from `content_sha256`; do not duplicate the same report.
- The 46-month historical backfill remains a later phase. Unavailable historical periods must stay explicitly unavailable.

## Access-control gate for the dashboard

The repository and GitHub Pages site are public. Do not add anonymous SELECT policies to financial tables. Before rendering live Supabase data in the dashboard, wire an authenticated, allowlisted read path (Supabase Auth/RLS or a protected Edge Function). Until then, the daily comparison panel displays an empty state instead of fabricated or unapproved values.
