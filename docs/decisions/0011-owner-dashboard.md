# 0011: The owner's figures and the renewals list

Date: 2026-11-16
Status: Accepted

## Decisions (`apps/dashboard`)

- **The dashboard only reads.** Each number is worked out by the module that
  owns the data (members, billing, attendance) through its services. The
  dashboard decides which figures to show and for which dates. It sits at the
  top of the import rules.
- **Figures for an application the gym has not switched on are left out**, not
  shown as zero.
- **Who sees what:** owner and branch manager see the figures (they include
  revenue). Owner, branch manager, receptionist and sales representative see
  the Renewals list (reception makes the calls). Trainers see neither.
- **Shown on the Overview page** through a separate HTMX request, the same way
  the member page shows payments and visits. Also available as its own page.
- **Each figure states what it counts**, in small text under the number.

## Definitions

| Figure | Counts |
|---|---|
| Active members | Members with a membership valid today (each person once) |
| New this month | Members registered from the 1st of this month to today |
| Ending within 7 days | Memberships valid today whose last day is within the next 7 days (today is day 1), with nothing bought to follow |
| Lapsed in the last 30 days | Memberships whose last day was in the 30 days before today, with nothing bought to follow (each person's latest only) |
| Collected today / this month / last month | Payments received on those days, leaving out reversed payments |
| Owed by members | The ledger total for the gym |
| Collected by payment method | As "collected this month", per method |
| Sold by package | Value of memberships invoiced this month, paid or not |
| Check-ins today / this month | Visits recorded |
| Not seen for 14 days | Members with a membership valid today, registered at least 14 days ago, with no visit in the last 14 days |

"Sold" and "collected" are different on purpose: one is what members agreed to
pay, the other is cash received. Both are labelled on screen.

## Not done yet

- Choosing other dates or months, and comparing months beyond "last month".
- Filtering by branch or by package.
- Trends over time (charts by week or month), peak hours.
- Exporting figures.
- Retention and renewal rates as percentages.
- Lead and trial figures (with Sales and Growth).
