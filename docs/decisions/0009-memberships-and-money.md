# 0009: Memberships, invoices, payments and the ledger

Date: 2026-11-16
Status: Accepted

## Memberships (`apps/members`)

- **A membership is one purchase of a package**, valid from `start_date` to
  `end_date` inclusive. It keeps its own copy of the package's name, price and
  length, so later package changes do not rewrite history.
- **Status is worked out from the dates** (upcoming, active, expired), not
  stored. Memberships therefore expire by themselves with no overnight job.
- **End dates:** a 1-day pass ends the same day; one month from 16 November
  ends 15 December; from 31 January it ends on the last day of February.
  The rules are in `durations.py` and are tested case by case.
- **No overlapping memberships** for one member. A renewal starts the day after
  the current one ends.
- **Start date** may be up to 31 days in the past or 366 days ahead.

## Money (`apps/billing`, the Payments and Finance application)

- **Invoice** (what is owed), **Payment** (what was received, with the receipt
  number), **Allocation** (which invoice a payment went towards), **Reversal**
  (cancels a wrong payment) and **LedgerEntry** (the member's statement) are
  separate records.
- **All five are permanent.** Database triggers refuse UPDATE, DELETE and
  TRUNCATE for every database user, and the web role has no permission to try.
  Corrections are new records.
- **The ledger is a per-member statement, not double-entry accounting.** Each
  line is a signed change to what the member owes: invoice +, payment −,
  reversed payment +. The balance is the sum. This is enough for a gym's needs
  now; exporting to accounting software is a later concern.
- **A reconciliation rule is tested:** the ledger total always equals invoices
  minus non-reversed payments, and payments equal their allocations.
- **Amounts are whole RWF integers.**
- **Idempotency:** each form carries a one-time key. The same key arriving
  again returns the first result. Money changes for one gym are also made one
  at a time (a row lock), so two people saving together cannot clash.
- **Numbers:** invoices (INV-000001) and receipts (RCT-000001) count from 1 per
  gym, with no reuse.
- **A sale is all or nothing:** membership, invoice, ledger line, optional
  payment and audit entries are saved in one transaction.
- **A membership is valid even if not fully paid.** The balance is shown on the
  member's page and on the Payments page. A gym setting to block access until
  paid can be added if pilot gyms want it.
- **Payments cannot exceed what is owed.** There are no credit balances yet.
- **Non-cash payments need a transaction reference.** They are recorded by
  staff and are not verified with MTN, Airtel or a bank. The screens say so.
- **Only the owner can reverse a payment**, with a reason. Front-desk roles can
  sell and record payments.
- **The receipt is a print-friendly page.** Browser printing gives paper or PDF.
- **Everything requires the Payments and Finance module** to be switched on,
  checked in the services and on the screens.

## How the member page shows money

`members` does not import `billing` (the import rules forbid it). The member
page asks the billing module for its "Payments" part with a separate HTMX
request and places it inline. If billing is switched off, that part is absent.

## Also changed

- One-time notices are stored in the session, not a cookie, so a notice still
  waiting to be shown is discarded when someone else signs in on the same
  computer. A test found a sale notice reaching the next user.

## Not done yet

- Refunds (money handed back), credit balances, discounts.
- Cancelling a membership or voiding an invoice; freezing and extending.
- Payments dated in the past; a daily cash-up report; revenue by method.
- Live mobile-money collection.
- PDF or shareable receipts; the member's own view of receipts.
- Check-in, and the Excel import and export.
