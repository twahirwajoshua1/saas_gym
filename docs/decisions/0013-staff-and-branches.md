# 0013: Staff and branch screens

Date: 2026-11-17
Status: Accepted

## Decisions

- **New module `apps/team`** holds staff management. It needs people, gyms and
  accounts together, and gyms and accounts may not import each other, so it
  sits above them (beside members) in the import rules.
- **Owner only.** Owners add staff with a role (and a branch if the gym has
  several), change roles, remove access, and reset passwords. Owners add and
  rename branches.
- **New staff get a login with a temporary password** (12 easy-to-read
  characters), shown once on screen and never stored readably. They must
  choose their own password before doing anything else (a middleware sends
  them to "Choose your password"). Anyone signed in can change their password.
- **A person who already has a login keeps their own password** when added to
  another gym.
- **Someone who left can be added again**; their old staff record is
  reactivated, keeping history.
- **No lock-outs:** owners cannot change or remove their own access, and a gym
  always keeps at least one active owner.
- **Password resets are limited.** A login belongs to a person, not a gym. An
  owner may only reset the password of someone who works at no other gym and
  is not platform staff; otherwise one gym's owner could take over an account
  used elsewhere. Resets also clear any sign-in lock.
- **Audit entries** for staff added, changed, reactivated and removed, logins
  created, passwords reset, and branches added or renamed. These are written
  with the gym, so they work under tenant isolation (lock 2).
- Branches are not closed or deleted yet (members belong to a home branch).

## Known limits

- A member registered at the desk can later be added as staff and given a
  login. When members get their own sign-in (member app), this must be
  revisited so a gym cannot set a member's personal password.
- Staff cannot reset their own forgotten password without the owner (needs SMS).
