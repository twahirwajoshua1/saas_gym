# 0004: Gyms, roles and tenant isolation (lock 1)

Date: 2026-11-16
Status: Accepted

## Decisions

- **A Gym is the tenant.** Every table whose rows belong to a gym inherits from
  `GymOwnedModel`, which gives it a `gym` column and a `.for_gym(gym)` query.
- **Code that reads gym-owned data always starts with `.for_gym(gym)`.**
  Looking up one record by its identifier is done inside that query, so another
  gym's record is "not found" even when its address is guessed.
- **Every gym screen is wrapped in `@gym_required`** (`apps/gyms/tenancy.py`).
  It is the single place that decides which gym a request acts for and whether
  the person's role allows the screen.
- **The chosen gym is kept in the session only as a hint.** It is checked
  against the database on every request, so removing a role or closing a gym
  takes effect immediately and a tampered session opens nothing.
- **A role belongs to a person at one gym** (`StaffAssignment`): owner, branch
  manager, receptionist, sales representative or trainer. One role per person
  per gym. An assignment may be limited to one branch.
- **Every gym has at least one branch**, created with the gym, so
  single-location gyms need no extra setup and multi-branch needs no
  restructuring later.
- **Staff are removed by switching the assignment off**, not by deleting it,
  so the history is kept.
- **Gyms are created through the `create_gym` service** (for now via a
  management command), never directly in the admin, so a gym always has a
  branch and an owner.
- **`tests/test_tenant_isolation.py` holds the isolation tests.** Each new
  gym-owned feature adds its own cases there.

## Not done yet

- Lock 2: PostgreSQL row-level security.
- Module switches (which gym has bought which application).
- The audit log.
- Screens for owners to add branches and staff (services exist; screens come
  with the Gym Operations module).
- Limiting what a branch-limited person sees to their branch.
