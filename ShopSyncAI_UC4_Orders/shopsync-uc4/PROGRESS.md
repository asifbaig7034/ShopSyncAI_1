# UC-4 Manage Orders Progress

Updated: 9 November 2026

The attached UC-4 project was updated in place: its existing `data/shopsync.sqlite3` and Windows `.venv` were preserved in the full archive. Use the companion code-only ZIP to update the same working folder without touching either directory. Keep `data/` and `.venv/` out of Git.

## Scope and status

This folder implements **UC-4 Manage Orders** as an independently runnable FYP module. The order API and dashboard use a small local catalog and a command-based customer simulator so order workflows can be demonstrated before the team's other use cases are integrated.

| UC-4 requirement | Status | Evidence or next step |
| --- | --- | --- |
| Owner creates an order; customer submits an order from chat | Implemented | Owner draft form and customer `ORDER` command; signed WhatsApp webhook adapter is configurable. |
| Collect product, quantity, delivery details, and payment method | Implemented | Customer must choose `COD` or `BANK_TRANSFER`; owner form offers both. |
| Show complete draft before confirmation | Implemented | Chat summary includes customer, item, unit price, quantity, address, payment choice, and total. Owner dashboard shows address and payment choice on the draft. |
| Edit or cancel a draft | Implemented | Owner edit endpoint and UI; customer `EDIT` and `CANCEL` commands. |
| Explainable, rule-based order risk score | Implemented | Score, reasons, and threshold stored on confirmation; high-risk orders remain on hold. |
| Approve or reject high-risk order | Implemented | Owner review; approval checks stock again before booking. |
| Confirm order with atomic stock and sale update | Implemented and tested | One SQLite transaction updates order, stock movements, and booked sale. Concurrent and duplicate requests tested. |
| Order status and unshipped cancellation | Implemented and tested | Status by customer number; cancellation restores deducted stock and reverses the booked sale once. |
| Return/refund request and owner decision | Implemented and tested | Eligibility window, full-order request, owner approval/rejection, financial adjustment; return stock restored only when the item is accepted. |
| Give a reason when rejecting a return/refund or returned item | Implemented and tested | Blank or whitespace-only reasons are rejected by the API; UI requires text; customer can read the reason via `STATUS`, and live notification includes it. |
| Save order when outgoing WhatsApp notification fails and retry | Implemented locally | Outbox records delivery state and retries; real Meta credentials and end-to-end live delivery still need testing. |

## Verification

Run `python -m unittest discover -s tests -v` from this folder. All **13 tests passed** on 9 November 2026. The suite covers important order-state transitions, stock safety, payment choice, summaries, and rejection reasons. Browser-based interaction and live WhatsApp delivery need manual checks in the deployment environment.

## Integration handoffs

| Team module | UC-4 integration point |
| --- | --- |
| UC-1 accounts and settings | Replace demo owner token with authenticated user and role checks; provide delivery options, fraud threshold, and return policy. |
| UC-2 orchestrator | Call the UC-4 order API for supported owner requests; use order IDs and status results. |
| UC-3 customer chat | Convert natural-language messages into validated UC-4 requests; use WhatsApp sender identity and UC-4 responses. |
| Product/inventory and finance modules | Agree on product IDs, available stock, and a single authoritative stock update; consume sale and reversal events when shared storage is integrated. |

## Remaining deployment work

- Test a real WhatsApp Business account, valid tokens, webhook delivery, and approved templates for messages outside its service window.
- Replace the local single-business SQLite store with the team's agreed shared persistence or integration layer before merging independent modules.
- Handle actual payment gateway refunds separately; the current module records a financial adjustment and flags manual payment action.
- Confirm whether the team needs partial-item returns; this prototype handles one full-order return/refund request per order.
