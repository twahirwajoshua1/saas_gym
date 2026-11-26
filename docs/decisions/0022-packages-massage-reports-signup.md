# 0022: Packages, massage, reports and signing up on your own phone

Date: 2026-11-26
Status: Accepted (built; for the founder's review with pilot gyms)

## The problem

Five separate complaints turned out to be the same complaint.

- A gym cannot sell what it actually sells. Somebody who only swims had to buy
  a membership that included the gym floor; "15 swimming tickets at 4,000"
  could not be expressed at all.
- The receptionist had a *Company visits* page nobody could explain. What a
  gym actually needs is the detailed monthly report it sends each company.
- Massage was being treated like a class. It is not: it is one person with one
  masseuse in a slot nobody else can have, where the kind of massage decides
  both the length and the price, and calling off late costs the gym money.
- A person who wanted to join had to queue at the desk while the receptionist
  typed their details in.
- A member's visit said only "Monthly". The gym could not see what they used.

## Decisions

### A package is what is included, a rule, and a price

- Every package lists the **services it includes**. A swimming-only membership
  is now a normal thing to sell.
- The rule is one of two kinds: **time** (use it as often as you like for a
  month, a year) or **count** (15 visits). A time package gives a membership;
  a count package gives a **ticketbook**. Visits may run out after a while, or
  never: the owner chooses per package.
- Memberships and ticketbooks keep their own copy of the name, the price, the
  rule *and the services included* on the day of the sale. Editing a package
  never takes a service away from somebody who already paid for it.
- A ticketbook on its own lets a member in. Somebody who bought swimming
  tickets and no membership is not turned away at the door.
- Taking a visit out of a book locks the book first, so two tills cannot spend
  the last visit at the same moment. A visit taken by mistake is **put back
  with a reason**; the use stays on record, marked cancelled.

### What a member did on a visit

- At check-in a member ticks what they are doing, from **what their package
  covers and nothing else**. At the tablet they do it themselves; at the desk
  the receptionist ticks while letting them in.
- Where a service comes from a ticketbook, the visit is taken straight away,
  so the count cannot drift from what was used.
- A service ticked by mistake is taken off **on the day only**, with a reason,
  and the row stays. After that day the visit is closed.

### The same rule for company visits

- A company visit can be corrected **only on the day it happened**, from Home.
  After that nobody can change it, not even the owner. A month a company has
  already been shown cannot move behind their back.
- Corrections are never deletions: the service stays on the report, crossed
  out, with who took it off, when, and why. The company sees the correction.

### Reports replace the Company visits page

- A single **Reports** page, filtered by company, by activity, by period and by
  the last month included, downloadable as **PDF or Excel**.
- Periods are **whole months only** (1 to 12), because a company pays for whole
  months. A half-month report would never match a bill.
- What the company receives is **detail, not a total**: date and time, staff
  number, name, department, branch, activity and amount, one line per activity,
  with the summary at the end.

### Massage

- A **massage type** carries the length and the price, so picking the type
  decides everything else. There is no separate "how long" question.
- Each type is **also a service**, so the door price and the price a company
  agreed in its contract live in one place. A massage a contract does not cover
  is simply paid for by the employee.
- A **request holds nothing** until the gym accepts it. Accepting takes the
  slot; a time already taken is refused, and the desk is offered the nearest
  free times instead.
- Free times come from the masseuse's working hours, the length of the massage,
  and a **gap kept between massages** (10 minutes by default). Times already
  gone today are not offered.
- **Calling off** is free until the cut-off (2 hours by default). After that the
  booking is marked as not come and the **full price is still charged**, because
  the masseuse held the hour and turned other people away. The cut-off is in the
  gym's settings so it can go in a contract.
- A masseuse's **note about a person is hers alone**. Not the receptionist, not
  the owner, not the company. It is about somebody's body. A visit with no note
  shows nothing at all.
- Masseuses and coaches get a **My day** page: the next session, today's list,
  their hours and their own numbers.

### Signing up on your own phone

- A gym has one **public sign-up link**, printed as a QR poster at the door. It
  carries the gym, not a person, so anybody may use it.
- The form asks for name, phone, gender, date of birth and an emergency
  contact, then a package. **Two agreements, kept apart**: one is needed to run
  the membership; the other, optional, is being sent greetings and offers.
  Birthday messages and promotions may only go to people who ticked the second.
- Filling it in creates **nothing but a place in the queue** and a four-digit
  code. No member, no membership, no money. The person walks to reception, says
  the code, pays, and one press registers them, sells the package and records
  the money together.
- The public page is rate-limited (40 sign-ups an hour per gym) because anybody
  can reach it, and it never writes to the shared people table.
- When mobile money is connected, the payment step moves to the person's phone.
  Until then the system does not pretend to have taken any money.

### Wording

- One word everywhere: **membership**. "Renew membership", "Sell membership",
  and the column on the Members page.
- **Sessions**, not Classes, in the menu: group sessions, personal training and
  massage, as three tabs.
- The receptionist's menu is **Home, Members, Sessions, Reports, My shift**.
  Register member is a button on Home, not a menu item.
- The Members page shows the gym's own members **and the staff of its
  companies** in one list, with a Company filter. At the desk a person is just
  somebody at the door. Membership tabs leave company staff out, because their
  company's contract covers them rather than a membership of their own.
- Tabs are All members, Active, Ending in 7 days, Ended recently. "Never
  bought" is gone: it was a list nobody worked from.

### What was removed

- **"Nothing paid yet"** on a sale. A sale is paid in full or in part; a
  membership starts when the money is in.
- **The transaction reference.** Typing a long provider id at the desk produced
  more wrong references than right ones, and a wrong one is worse than none.
  The field stays in the database so payments recorded before this keep theirs,
  and so a provider can fill it in once mobile money is connected.

## What this costs

- The Members page moved from the members app to the front-desk app, because it
  now shows company staff and the layer order only allows that upwards.
- Massage is a new app above corporate, because a booking may be for a member
  or for a company's employee.
- `reportlab` is a new dependency, for the PDF a gym sends a company.

## Still open

- Promotions and discounts, follow-ups, automatic birthday and milestone
  messages, and the Pro Shop are designed but not built.
- A member cannot yet ask for a massage from their own phone; until the member
  side is built, the receptionist records the request.
- Streaks for coaches and masseuses are agreed in principle; how "good
  performance" is measured is not decided. Counting sessions alone rewards
  squeezing in short bookings, and counting reviews alone invites pressure on
  clients.
- Whether this system is a data controller or a data processor under law
  058/2021, and what that means for hosting and registration, is a question for
  the NCSA or a Rwandan data-protection lawyer. Nothing here depends on the
  answer; it decides where the server lives.
