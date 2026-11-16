# 0005: Tenant isolation, lock 2 (row-level security)

Date: 2026-11-16
Status: Accepted

## Decisions

- **PostgreSQL enforces the gym boundary itself**, in addition to our code
  (lock 1, decision 0004). If code ever forgets to filter by gym, the database
  still returns only the current gym's rows.
- **Web requests run as a restricted role, `gymos_app`.** It cannot log in, is
  not a superuser and cannot bypass row-level security. Middleware
  (`apps/core/middleware.py`) switches to it with `SET ROLE` at the start of
  each request and back at the end. Because it is a role switch, there is no
  extra password or setting for developers.
- **The database is told who is asking and which gym**
  (`app.current_person_id`, `app.current_gym_id`). The gym is set by
  `@gym_required` only after the person's role has been verified. With no gym
  set, no gym-owned rows are visible at all (fails closed).
- **Policies live in migrations.** A test fails if any table inheriting
  `GymOwnedModel` lacks row-level security and a policy, so a new gym-owned
  table cannot be forgotten.
- **A person can always see their own staff records**, across gyms. This is how
  the list of "gyms I work at" is built before a gym has been chosen.
- **`select_related("branch")` is not used when listing a person's gyms.** A
  join would silently show "no branch" for rows hidden by the policy, which
  would turn "limited to one branch" into "all branches".
- **Row-level security is not FORCEd.** The table owner (the migration user)
  is not restricted. This is intended: migrations, management commands and
  the platform admin work across all gyms.

## Deliberate bypasses (review anything new that uses them)

1. Migrations and management commands (`create_gym`, `create_platform_admin`)
   run as the normal database user.
2. Signed-in platform staff (`is_staff`) opening `/admin/` pages.
3. The status page, which must work when the database is down and touches no
   gym data.
4. Background jobs, when added, will need an explicit, logged way to act for
   one gym at a time.

## Known gaps

- `Person` and `User` are shared across gyms by design and have no policy.
  They must only be reached through services. When gym members are added,
  each gym's member list will be a gym-owned table.
- `Gym` itself has no policy (it is the directory of tenants and will be
  needed for gym discovery).
- Code that can run raw SQL could undo the role switch. Lock 2 protects
  against mistakes in our filters, not against malicious code.
- Use of the platform admin bypass is not recorded in our audit log (see 0006).
