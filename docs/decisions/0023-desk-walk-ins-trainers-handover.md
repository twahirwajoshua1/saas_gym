# 0023: Walk-ins, receipts on WhatsApp, adding a trainer, handing the desk over

Date: 2026-11-27
Status: Accepted (built; for the founder's review with pilot gyms)

## The problem

Six complaints from watching the receptionist's screens.

- The desk was being asked which branch it was at. A receptionist works at one
  branch; being asked implies they could answer wrongly.
- A paid receipt could only be printed. Paper costs money and gets lost.
- Somebody walking in for one sauna had nowhere to go in the system. The
  receptionist either registered them as a full member, which is wrong, or
  took the money off the books, which is worse.
- The timetable had a *Previous week* link nobody pressed, and did not say
  the thing a gym actually repeats: spinning at Hilltop every Wednesday at
  7:30.
- A new trainer could not start work until the owner had time to add them.
- A shift could be closed with people still waiting, and nobody would know.

## The decisions

### Where somebody works is part of their job, not a question

The branch comes from the staff record. Owners and branch managers, who belong
to no one branch, still choose. A receptionist with no branch set is told so
plainly rather than being guessed at.

### The receipt goes out as a message the gym sends itself

`receipt_message` writes the text; the page offers *Send on WhatsApp* and
*Send by SMS* as `wa.me` and `sms:` links from the gym's own phone, beside
*Print*. Nothing is sent by the system, which is the rule everywhere: the gym
sends its own messages. A receipt fits in a message; a monthly company report
does not, so that one stays a file with a link.

### A walk-in is a member record with no membership

`Invoice`, `Payment` and `LedgerEntry` all belong to a member, and loosening
that to let money float free would weaken every balance in the system. So a
walk-in gets a plain record: a name and a phone number, nothing else. They
hold no membership, so they appear in none of the active, ending-soon or
lapsed lists. Coming back finds the same record by phone number, which is how
the desk can see a regular and offer them a package.

The whole amount is taken at the door. There is no part payment for a sauna
somebody has already had.

### A walk-in's massage is paid for when the time is held

A booked slot is a masseuse sitting waiting, so the money and the booking
happen in one transaction: both, or neither. If the time has gone between the
desk reading it off the screen and pressing the button, nothing is saved and
the desk is told to offer another time. There is then no no-show to chase.

This operation needs massage, members and billing at once, so it lives in
`frontdesk`, the only app above all three.

### The timetable says what happens every week

`weekly_pattern` reads four weeks of sessions and calls a slot weekly when the
same class, time, length and branch appear in at least two different weeks.
Two weeks, not four, so a class that started last month still shows. It is
read from what the gym actually did, so nobody has to maintain a second list.

### The desk can add a trainer, but cannot let them in

`add_trainer` is open to the front desk: the desk hears about a new trainer
first, and until the trainer exists nobody can put them on the timetable or
book a massage with them. No account is created. A way into the system stays
the owner's to give, on the staff page — a receptionist who could create
logins could create one for themselves.

### Closing a shift finishes nothing, and says so

Somebody going home at seven cannot be kept there by a massage request nobody
has answered. So the close is never blocked. Instead the page lists what is
still open — people who signed up and have not paid, unanswered massage
requests, today's invoices with money still owed — and the person ticks to say
they have seen it. The counts go on the closed shift, so an owner can see a
desk that leaves work behind every evening. The work itself stays on the home
page for whoever is on next, where they can pick it up where it was left.

The page also says that a shift is best closed once the replacement has
arrived, so the drawer and the unfinished work change hands together.

## What this does not do

- Mobile money is still typed in by hand. No transaction reference is asked
  for: the number is long, nobody reads it off a phone correctly, and a wrong
  one is worse than none.
- A walk-in must have a phone number. It is how the gym knows them again and
  how the receipt reaches them.
- The handover counts what is open; it does not measure how long it stayed
  open. If gyms start handing the same thing over night after night, that is
  the next thing to measure.
