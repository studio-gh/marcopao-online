# Telegram bridge (Marcopão Online)

The scheduled collector connects to the authorized Telegram session, navigates the actual Bellabyte menu, and writes a report's raw text plus parsed totals into the protected Supabase `report_imports` table with status `needs_review`. It never marks imported numbers as validated automatically.

## Collector workflow

- GitHub Actions workflow: [Marcopão report collector](https://github.com/studio-gh/marcopao-online/actions/workflows/monitorbot-monthly-probe.yml)
- Manual modes: `daily_yesterday` and `monthly_october`.
- Scheduled run: daily at 03:10 UTC, which is 00:10 in `America/Sao_Paulo`. It requests the previous day's report.
- The bot's menu is stateful. If the conversation is in an unexpected state, the collector uses the documented `0` reset, reselects the branch, and verifies the main menu before continuing.
- The report and credentials are not printed in full in public GitHub Actions logs.
- The collector is idempotent by `content_sha256`; duplicate raw reports are ignored by PostgREST upsert.
- The collector stops before opening Telegram if the Supabase service-role secret is missing.

## Required GitHub Actions secrets

Set these in **Settings → Secrets and variables → Actions**:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION_STRING`
- `BELLABYTE_PASSWORD`
- `SUPABASE_SERVICE_ROLE_KEY`

The Supabase secret must be the project's **service_role** JWT key used only by the workflow. Never commit it, put it in a client-side page, or paste it into chat. The repository is public, so do not log raw sales reports or upload raw reports as public Actions artifacts.

## Data and validation

The collector stores:
- Source bot and branch
- Report type and requested period
- Message ID and timestamp
- Raw text and SHA-256 content hash
- Parsed totals and reconciliation warnings
- `status = needs_review`

This is a capture/import stage, not approval. Review report semantics, month-to-date vs. closed-month labeling, cancellation values, and totals before promoting anything to `validated`.

## Dashboard status

The GitHub Pages dashboard authenticates users with Supabase Auth and checks `dashboard_access` before querying `report_imports`. The daily comparison uses actual daily reports only, shows a date filter for a specific day, and compares that date with the immediately previous day for the same branch. Status `needs_review` remains visibly pending review. Do not add anonymous read policies to financial tables.

## Next steps

1. Add `SUPABASE_SERVICE_ROLE_KEY` to GitHub Actions secrets.
2. Run the collector manually once with `daily_yesterday` to collect yesterday's report.
3. Verify the resulting `report_imports` row remains `needs_review`.
4. Wire the dashboard to Supabase through an authenticated read path.
5. Once two consecutive daily reports exist, verify the day-over-day dashboard with the real data.
6. Only then scale the historical backfill (January 2023 onward). Never invent unavailable periods.

