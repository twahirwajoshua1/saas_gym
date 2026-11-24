# Working on Gym Business OS

Read this before doing anything in this repository.

## Who this is for

Joshua Twahirwa is building a multi-tenant SaaS for Rwandan gyms: memberships,
check-in, money, corporate wellness contracts, classes. He is the owner, the
product decision-maker, and a Python developer who reads every line. He is not
a Django expert, so explain things plainly.

## Ask before you act

These are hard gates. Breaking them wastes his time and his trust.

1. **Do not write code until he has approved what you are about to build.**
   Describe the change first — what it does, which files, what it means for the
   existing design — and wait for a yes. "Explain before building" is his
   standing instruction, not a preference.
2. **Do not run `git commit` until he has approved it.**
3. **Do not run `git push` until he has approved it.** Separately from the
   commit. Approval of one is not approval of the other.
4. Answering a question, reading code, running tests, and taking screenshots of
   what already exists do not need approval. Changing files does.

When he says "build now", or names a specific thing to build, that is approval
for that piece of work only. It is not a standing licence for the next idea.

## How he wants to be talked to

- Plain language. He asked for "beginner-friendly explanations" and means it.
  Say what a thing does before you say what it is called.
- Short. Lead with the answer or the deliverable. No preamble.
- Honest about limits. If something is faked, partial, or will not work on his
  machine yet, say so in the same breath as the good news.
- Screenshots of the real running app, not mockups, when showing UI work.
- Never say the work is done when tests are failing or a step is unverified.

## Rules that are never broken

These come from his explicit decisions. If a request seems to require breaking
one, stop and say so rather than quietly working around it.

- **Two locks on tenant isolation.** Every query on gym-owned data goes through
  `.for_gym(gym)` *and* the table has a PostgreSQL row-level-security policy
  against `app_current_gym_id()`. A new gym-owned table is not finished until it
  has both, plus a migration adding the policy. `tests/test_tenant_isolation.py`
  enforces this for every `GymOwnedModel`; do not weaken that test.
- **Financial history is never silently overwritten.** Payments, invoices,
  bills, receipts and closed shifts are permanent: database triggers and
  `REVOKE` stop updates and deletes. Corrections are made by reversal, with a
  reason recorded. The same applies to corporate bills and to anything that
  decides what a company owes.
- **Never fake an integration.** No pretend SMS sending, no pretend mobile money,
  no pretend EBM. Money goes to each gym's own MoMo or merchant account; each
  gym sends its own SMS and WhatsApp. Where the system cannot do something, it
  opens the gym's own tool (for example a `wa.me` or `sms:` link with the
  message written) rather than pretending to have sent anything.
- **Personal data stays in Rwanda** unless the NCSA authorises otherwise
  (law 058/2021, article 50). This decides hosting. The work server may only
  ever hold sample data, and only with his employer's agreement.
- **Demonstration data uses real institution names** (WASAC, REG, RTDA, REMA,
  MINAGRI) because he asked for them, but every person, phone number, visit and
  amount is invented. That database must not be exposed publicly.
- **His personal phone number is never written into the repository.**

## How the code is organised

A modular monolith: one Django project, one PostgreSQL database, separate apps.
See `docs/decisions/0001-architecture.md`.

- Business rules live in each app's `services.py`. Views collect input, call a
  service, and render. Views contain no rules. `pyproject.toml` enforces this.
- Modules may only import downwards. The layer order is in `pyproject.toml`
  under `importlinter`. A new app is added to that list when it is created.
  Run `lint-imports` to check.
- Services take `by: StaffAssignment` and call `require_role(...)`. Bad input
  raises `InvalidDetails(field, reason)`; views turn that into a message.
  Anything worth explaining later goes to `audit.record(...)`.
- Roles: OWNER, BRANCH_MANAGER, RECEPTIONIST, SALES_REP, TRAINER.
  `MANAGER_ROLES` is owner and branch manager.
- Only two views of the gym: the owner view (owner and branch manager) and the
  desk view (receptionist). Do not invent a third.
- Each of the nine applications can be switched on per gym; check with
  `module_services.is_enabled(gym, CODE)` before showing a feature.

## Finishing a piece of work

Not done until all of these pass:

```
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/lint-imports
```

- Every feature has tests, including the ones that prove a rule cannot be
  broken (over-filling, double-booking, acting on another gym's data).
- A decision worth remembering gets a numbered record in `docs/decisions/`.
- PostgreSQL in a fresh container often needs `service postgresql start`.

## Running it

```
docker compose up -d
python manage.py migrate
python manage.py create_sample_gyms     # fresh database only; ~4 minutes
python manage.py runserver
```

With `DEBUG=True` and `DEMO_LOGINS=True` in `.env`,
`python manage.py create_demo_logins` adds short names for demonstrations:
`gymowner`/`gymower`, `receptionist`/`receptionist`, `member`/`member`,
`company`/`Company`. They never work in a real installation.
