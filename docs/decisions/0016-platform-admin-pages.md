# 0016: Platform admin pages

Date: 2026-11-17
Status: Accepted

## Decisions (`apps/platformadmin`)

- **For platform staff only** (`is_staff`), at `/platform/`. Gym staff get 403.
- **Gyms list** with owners, member counts and how many applications are on.
  No members' personal data is shown.
- **Register a gym** with its name, owner name and owner phone. This creates
  the gym, its main branch, the owner's role and, if the owner has no login,
  a login with a temporary password shown once (changed at first sign-in).
- **Switch applications on and off** with the same dependency rules as the
  command line. Every switch is in the gym's audit log with the platform
  person as actor.
- **Suspend and restore a gym.** Suspending needs a reason; nobody can open the
  gym while suspended; nothing is deleted.
- **Tenant isolation:** these pages are a recorded bypass. The middleware runs
  `/admin/` and `/platform/` requests from signed-in platform staff as the
  normal database user, so they can work across gyms (decision 0005).
- The `set_module` and `create_gym` commands still work for scripts.

## Not done yet (Step 7)

Plans, prices, subscriptions and billing gyms for the platform, usage limits,
logged support access to a gym's data, and onboarding without platform staff.
A suspended gym's member card links still open.
