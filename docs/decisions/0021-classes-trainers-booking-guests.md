# 0021: Classes, trainers, booking, reviews and bringing a friend

Date: 2026-11-24
Status: Accepted (built; for the founder's review with pilot gyms)

## The problem

Members do not know who is teaching, when, or what a class will be like, so
classes run half empty while the floor is crowded. Gyms have no record of who
is coming, trainers have no way to be chosen, and a member who brings a friend
is handled differently by every receptionist.

## Decisions

- **Classes are a module** (`Training and Classes`, already in the catalog). A
  gym that has not bought it sees none of this, and the member's card hides it.
- **What the gym teaches** is written down once (name, what to expect, how long,
  how many places) and put on the timetable as often as wanted, with a
  "repeat weekly" of up to 12 weeks. Running the same schedule twice adds
  nothing, so a tired manager cannot double-book the room.
- **Booking takes a real place.** The class row is locked while a place is
  taken, so two phones cannot take the last one. Booking closes 30 minutes
  before the class starts, which is also when a member can no longer give a
  place back.
- **Giving up a place keeps the row** and sets a cancelled time, so a gym can
  see who books and never comes.
- **Members pick a trainer.** A trainer writes a short page in their own words
  (no photo, as decided in 0020) and their free hours by weekday. Those hours
  are cut into one-hour slots; hours already taken by a one-to-one or by a
  class they are teaching are not offered. Money for a one-to-one is the gym's
  own arrangement at reception; nothing is sold here, because we do not model
  what we have not agreed.
- **Reviews are public**, as the founder chose. Only a member who had a place in
  a class that has already finished may review it, once, so the score cannot be
  stuffed. A manager can hide a review but must give a reason, and both the
  review and the reason are kept in the activity log: this cannot be used
  quietly to bury criticism.
- **Bringing a friend is each gym's own rule**: a number of free guests per
  member per calendar month, where zero means the friend pays the day price.
  The desk cannot give away more than the owner allowed.

## Privacy

A guest is not a member and has signed nothing. The gym keeps a name and, only
if the friend offers it, a phone number, and nothing else. This is personal
data of a non-customer under law 058/2021, so it stays inside the gym's own
rows (row-level security, like everything else) and should be cleared out on a
schedule once we agree one with the pilot gyms.

## Not done (yet)

- Nobody is charged for a one-to-one inside the system.
- Company employees cannot book classes yet; only members can.
- Community content (requested workout videos, nutrition) is the next piece.
- No reminder is sent automatically for a booked class; the desk's "Reach out
  today" is still a person pressing WhatsApp.
