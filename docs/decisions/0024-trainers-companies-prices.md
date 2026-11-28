# 0024: A trainer's own home, companies by company, and a price grid

Date: 2026-11-28
Status: Accepted (built; for the founder's review with pilot gyms)

## The problem

Six complaints from reading the owner's and the masseuse's screens.

- A masseuse signing in landed on the receptionist's desk, so she was shown
  who had arrived and who to telephone — neither of which is her job.
- A trainer who only takes classes could open the massage diary and read
  clients' names.
- The companies page led with today's visitors and buried what companies owe.
- Today listed one row per person, so five companies became fifty names.
- A gym could only write a contract with a company already on the platform,
  using a code it had to ask that company for.
- Thirty staff over four branches was one unfiltered list.
- Thirty packages had to be created one at a time, by hand.
- The applications page ended at "contact the platform team", which is a wall.

## The decisions

### Everybody lands on their own work

The code sent everyone who was not an owner to the desk. Now a trainer goes to
their own page: a masseuse to her day, a trainer who only takes classes to the
timetable. The desk's arrivals and follow-up cards are hidden from a trainer
even at the desk's own address, and the desk is no longer in their menu.

### Massage is closed to a trainer who does not give massages

`gives_massages` is written on the trainer's page, so the check belongs in the
`classes` app. A context processor there tells the menu, and a decorator on
every massage page refuses anyone else. The desk and managers keep their way
in, because they book massages and answer requests.

### The masseuse is shown who has stopped coming

Six weeks, longest away first, at most ten names. Only her own clients, only
massages that actually happened, and anybody already booked in is left out —
chasing somebody you are seeing on Tuesday is embarrassing. The message is
written for her and opens on her own phone: the gym sends its own messages.

### Companies: money first, and counted by company

Bills not fully paid, then contracts, then today. Today is one line per
company with a count, opening to show what they came for. A gym talks to
WASAC, not to each of WASAC's four employees.

While doing this, the "Not confirmed yet" badge turned out to be dead: every
company visit is confirmed the moment it is created, because the card scan or
the PIN is the signature. It has been removed rather than carried forward.

### A gym can put a company on the platform itself

A gym signs a paper contract with WASAC, and WASAC has never heard of the
platform. Until now the gym had to ask WASAC's logistics officer for a code
that did not exist. Now the gym registers the company from the contract page,
giving the name, the TIN, and who signed for them. That person gets a
temporary password, which the gym hands over, because somebody at the company
still has to approve the contract and add their staff.

The contract also carries the paper's reference number, free text, because a
Rwandan company numbers its contracts how it likes and the gym has to be able
to quote it back on a bill.

### Staff can be cut to one branch and one role

Somebody with no branch of their own works at every branch, so they show
whichever branch is asked for. They are at that branch too.

### Prices are a grid, not thirty forms

One row per service, columns for day pass, monthly, quarterly and annual. A
filled box is a package the desk can sell; an emptied box stops that package
being sold, archived rather than deleted, so everyone who already bought it
keeps what they bought.

A package the grid owns is recognised by its shape — one service, for one of
those lengths — rather than by a flag, because that shape is exactly what a
box means. A package covering several services belongs to the builder below
the grid and is never touched from here.

Tickets are not a grid column. A gym sells 5, 10 or 20 at prices that are not
multiples of each other, because the bigger bundle is cheaper per visit. So
each size is its own package, added on its own line.

### Asking for an application is a button

The page shows every application, on or off, because that is how an owner
finds out corporate wellness exists. Each one that is off now has **Ask for
this**, which records who asked and when. Asking switches nothing on and
changes nothing about what the gym pays. It puts the gym on a list the
platform side can act on, instead of waiting for a phone to ring.

## What this does not do

- The grid does not group services into categories. Tennis, basketball and
  football rarely cost the same, and grouping them would either force one
  price across all three or add a second question at every sale.
- A company registered by a gym still needs its officer to approve the
  contract and add staff. The gym cannot add another company's employees.
- The masseuse's lapsed list counts weeks away; it does not measure whether
  the message worked. If gyms start using it, that is the next thing to
  measure.
