# 0007: Import rules, wrong-password limit and automatic tests

Date: 2026-11-16
Status: Accepted

## Import rules

- **`lint-imports` (import-linter) checks two rules** set in `pyproject.toml`:
  1. Modules depend on each other in one direction only:
     `modules` > `gyms`, `accounts` > `audit` > `people` > `core`.
     `gyms` and `accounts` may not import each other. A new application module
     is added to this list when it is created.
  2. Business logic (`services.py`, `models.py` and similar) never imports
     screens (`views.py`, `urls.py`, `forms.py`, `admin.py`).
- **`tests/test_architecture.py` checks that screens do not write to the
  database**: view and form files may not import `django.db` or call `.save()`,
  `.create()`, `.update()`, `.delete()` and similar. They call services.
  (import-linter cannot express this rule, so it is a test.)
- **Narrower than first planned.** The plan said a module would use another
  module "only through its service functions". In practice models must refer to
  other modules' models (a staff assignment points to a Person), and services
  use them as types. So the enforced rule is the direction of dependencies and
  the separation of logic from screens. Writing to another module's tables is
  still done only through that module's services, by convention and review.

## Wrong-password limit

- **5 wrong passwords for one phone number within 15 minutes locks sign-in for
  that number for 15 minutes.** During a lock the correct password is refused
  too. A successful sign-in clears the count.
- **It lives in the authentication backend** (`apps/accounts/backends.py`), so
  it covers our sign-in page and the platform admin sign-in. The backend also
  accepts the phone number written any way.
- **Counted per phone number, not per device or internet address**, because
  many people in a gym share one connection, and because the hosting setup
  (which decides how addresses are seen) is not chosen yet.
- **Numbers without an account are counted and locked the same way**, so the
  lock message does not reveal who has an account.
- **Our own small implementation, not a library.** Password checking and
  sessions remain Django's own. The counting is about 40 lines and is tested.
  `django-axes` is the established library if needs grow.
- `python manage.py unlock_sign_in --phone ...` removes a lock early and is
  recorded in the audit log.

Known limits:

- Someone who knows a phone number can keep that account locked by guessing
  wrongly on purpose. One-time codes by SMS and a per-address limit at the web
  server will reduce this.
- Trying one common password against many different numbers is not limited.
- A lock is written to the server log, not the audit log (the web role cannot
  write platform-level audit entries; see 0006).

## Automatic tests on GitHub

- `.github/workflows/tests.yml` runs on every push to `main` and every pull
  request: Ruff, `lint-imports`, Django's checks, a check that no migration is
  missing, and the full test suite against PostgreSQL 16.

## Scope decision

- **Organization registration moves from Step 2 to Step 6**, with the rest of
  corporate wellness. The Person and payer design already leaves room for it.

## Step 2 gate

Done: tenant isolation tests (both locks), import-rule check, audit log,
module switches, roles, shared identity, a basic platform admin.
Carried forward: sign-in by one-time SMS code, password reset, changing a
phone number, recording sign-ins in the audit log.
