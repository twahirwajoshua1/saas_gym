# 0020: Front desk, entrance tablet, streaks and the company league

Date: 2026-11-22
Status: Accepted (built; for the founder's review with pilot gyms)

## The problem

At busy hours (30 or more people) check-in is a queue at reception. Company
staff also sign paper lists per service, and some are told "go in and sign
later", which loses visits and makes the monthly bill hard to trust. Owners
cannot see at a glance what the day brought, and receptionists hand over cash
without a clear total.

## Decisions

- **Two gym views, not more.** Owners and branch managers see *Today*
  (clients, money today and this month, cash vs mobile money, what companies
  owe, advice). Receptionists see the *Desk* only: arrivals, problems, search,
  camera scan and their own shift. Same app, same URLs, menus decided by role.
- **A self-scan entrance tablet.** Any tablet with a browser, paired to one
  branch with a 6-digit code valid 15 minutes; afterwards it is known by a
  secret cookie (only a SHA-256 of the secret is stored). The tablet itself
  is the welcome screen; no television is needed.
- **The scan is the signature.** A company employee's card carries a code that
  changes every 30 seconds and works on one phone only, so a screenshot or a
  lent card does not work. The employee taps the services used today (Gym is
  ticked first) and walks in. A PIN is the fall-back for people without a
  smartphone (locked after 5 wrong tries in 15 minutes). "Sign later" and the
  after-visit confirmation are dropped.
- **Only problems reach the desk.** Ended or frozen memberships, unknown
  cards, removed employees, no contract and locked PINs appear under "Needs
  you" for three hours, with Renew and Done.
- **Weekly streaks.** A week counts with 2 visits; one missed week per month is
  forgiven. Bronze at 4 weeks, Silver 12, Gold 26. Shown on the tablet welcome
  and on the member's and employee's card. Nothing is stored: the streak is
  worked out from visits, so it cannot drift.
- **Company league.** Each month: the share of a company's listed staff who
  came 4 or more times, at any gym on the platform. Shown on the tablet strip,
  the employee card and the company portal (with departments). A company can be
  left out of the league (`in_league`). It is read through one narrow database
  function, so no company sees another's staff.
- **Shifts.** A receptionist sees cash, MTN MoMo and Airtel Money since their
  last close, and closes the shift to hand over. A closed shift is permanent
  (the database refuses changes), like payments.
- **The company officer** sees, per gym, the amount for each service (visits ×
  contract price) and how much is owed to each gym, with overdue dates.
- **"Owes" stays**, worded as money not yet paid on an invoice.

## Not done (yet)

- No photos of people (Rwandans dislike being photographed; the phone-bound
  card replaces the photo check).
- Tablet business model (lease, instalments, deposit) is a commercial choice
  for the founder.
- Discount option B (money-off codes) only if pilot gyms ask.

## Security notes

- The tablet is anonymous; every tablet request sets the gym from the tablet
  record through a SECURITY DEFINER lookup, and all tables keep row-level
  security.
- Rotating codes are HMAC-based, accepted for about 2 minutes.
