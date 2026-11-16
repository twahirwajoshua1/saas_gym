# 0006: Audit log and module switches

Date: 2026-11-16
Status: Accepted

## Audit log (`apps/audit`)

- **Permanent.** PostgreSQL triggers refuse to update, delete or truncate
  audit entries, for every database user including the migration user. A
  mistake is corrected by recording a new entry.
- **Written with the action.** Services record the entry inside the same
  database transaction as the action. If the entry cannot be saved, the action
  does not happen (tested).
- **Private per gym.** Entries carry the gym they belong to and are protected
  by row-level security (lock 2). The web role can add entries for its current
  gym and read them, and nothing else (no update or delete permission).
  Entries with no gym (platform-level, such as creating an account) are visible
  only to the platform owner.
- **`actor` is optional.** It is the person who did the action, or empty for
  the system or a platform command. Screens must pass `request.user.person`.
- **Details never hold secrets.** No passwords, codes or keys.
- Recorded so far: gym created, branch added, staff assigned and removed,
  account created, platform administrator created, module switched on or off.
- The owner sees the gym's entries on the Activity page.

## Module switches (`apps/modules`)

- **The catalogue of nine applications and their dependencies lives in code**
  (`catalog.py`), because it is the structure of the product. Prices and plans
  will be database data (Step 7).
- **Gym Operations is always on** and has no row. Every other application needs
  Gym Operations; the rest of the dependencies follow the plan's dependency
  diagram.
- **Switching on needs everything it requires; switching off is refused while
  another switched-on application needs it.** Both are checked in the service
  and tested, including that the dependency graph has no loops.
- **Switch-off keeps history.** The row stays, with when it was switched off.
- **Only the platform side can change switches.** The web role has read-only
  permission on the table, so even a bug in a gym screen cannot switch modules
  on. For now the platform owner uses `python manage.py set_module`. The Django
  admin shows switches and audit entries read-only, so it cannot skip the rules.
- **`@module_required(code)`** protects a screen. It goes under `@gym_required`.

## Not done yet

- Branch managers cannot see audit entries (entries do not record a branch yet).
- Platform-level audit entries cannot be written from a gym web request. When a
  screen needs that, add an explicit, reviewed way to do it.
- Sign-in and sign-out are not recorded.
- Use of the platform admin bypass is not recorded in our audit log.
- Plans, prices and subscriptions (Step 7).
