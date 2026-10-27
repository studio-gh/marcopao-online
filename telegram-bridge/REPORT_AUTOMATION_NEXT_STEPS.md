# Marcopão report automation: validation and rollout

## Verified from the October 2026 probe

- The Telegram bot returned the October 2026 monthly sales summary.
- The probe captured the report text from Telegram history and then answered `Não` to the card-details prompt.
- The bot returned to its main report menu.
- The probe did not write to Supabase.
- A green GitHub Actions run means the script exited successfully; it does not, by itself, mean that a report was parsed, validated, or stored.

## Safe next steps

1. Persist the exact bot response as a workflow artifact (UTF-8 JSON, with message IDs, timestamps, direction, requested period, and raw text). Do not print credentials or Telegram session strings to logs.
2. Parse the saved response into a structured JSON artifact. Preserve the original text and report metadata alongside parsed values.
3. Validate arithmetic and semantic assumptions before database import:
   - The October summary's payment-method values sum to the reported total.
   - The report contains cancellation and unfinished-sale figures that must not be silently merged with completed sales.
   - Confirm whether the report's monthly total is month-to-date or a closed-month figure. A query run during October must not be labeled as a final closed month without evidence.
4. Add automated tests for Brazilian currency parsing, repeated totals in separate sections, missing fields, duplicate snapshots, and payment total reconciliation.
5. Only after artifact review, import with idempotency based on a content hash and preserve raw text. Keep report status as `review_required` until validation passes.
6. Scale the collection to the historical periods only after the October end-to-end test is stable. Record unavailable periods as unavailable; never synthesize values.

## Menu navigation learned from the probe

The bot's conversation is stateful. It can ask for branch, report, year (Ano Atual/Ano Anterior), month, and whether to display card details. A workflow must inspect the latest bot prompt and select a matching visible option; it must not send numeric choices blindly. If the conversation is in an unexpected state, send `0` to return to branch selection, then re-establish the state from the new bot reply. If the bot does not reply after a valid choice, capture diagnostics and stop rather than guessing.

## Current release gate

No historical backfill and no production database writes until a complete artifact exists and the parser tests pass.