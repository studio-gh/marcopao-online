# Telegram bridge (Marcopão Online)

This bridge is the proposed local connector between the authorized Telegram account and the Marcopão Online database. Bellabyte has no official API, so this must be tested against the bot's actual conversation flow before enabling unattended imports.

## Why it runs locally first

- Telegram login codes and the authorized session stay on the bakery owner's Mac.
- No Telegram session file, API hash, password, Supabase secret, or service-role key belongs in GitHub.
- The first version must run in **read-only / export-only mode** and save raw replies with timestamps for review.
- Do not treat an in-progress monthly report as a final close. Store the query time and period.
- Do not write parsed data into the production tables until totals and duplicate handling are tested.

## Proposed phases

1. **Connect and observe:** sign in with the user's own Telegram account and read the existing conversation with `@MonitorMarcopaoBot`.
2. **Guided query test:** request one known report (monthly sales, October 2026) and capture the exact message sequence, including reply-keyboard interactions.
3. **Export-only parser:** store the raw response and a SHA-256 fingerprint locally; parse only known report formats.
4. **Validation:** compare parsed values to the raw message and record warnings rather than silently reconciling differences.
5. **Database import:** only after explicit approval, send validated data to a protected Supabase endpoint using a dedicated, least-privilege credential.
6. **Scheduled operation:** add scheduling only after the interactive bot flow is reliable and the account/session behavior is understood.

## Prerequisites for the local proof of concept

- Telegram Desktop installed and signed in on the Mac, or access to the Telegram account for a one-time login.
- Telegram API application credentials (`api_id` and `api_hash`) created by the account owner at https://my.telegram.org. These are used by the Telegram client library, not supplied by Bellabyte.
- Python 3.11 or later.
- The owner must approve the first interactive run and keep the session file private.

## Security rules

- Never commit `.session`, `.env`, Telegram login codes, API hashes, bot tokens, Supabase service-role keys, or exported private reports.
- Add local secrets and session files to `.gitignore`.
- Do not bypass Telegram login protections or attempt to access Bellabyte's server.
- If the bot's interaction cannot be automated reliably, use a user-approved export/import flow instead of pretending the bot is connected.

## Current status

Planning only. This document does not claim a Telegram connection exists or that any report has been fetched automatically.
