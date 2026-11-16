# 0001: Modular monolith on Django and PostgreSQL, API-ready

Date: 2026-11-16
Status: Accepted

## Decision

- One Django application and one PostgreSQL database, organised into separate
  modules (a modular monolith).
- Django templates with HTMX for the gym, organization and platform
  administration interfaces, and for the first member pages.
- Business logic lives in each module's `services.py`. Screens, API endpoints
  and background jobs call services and contain no business rules.
- The system is API-ready from the start so a React or React Native member app
  can be added later without rewriting the back end. The API is built only
  when there is an app to use it, apart from one small proof endpoint in the
  first commercial release.

## Priorities, in order

1. Modular and maintainable code. Each of the nine applications is its own
   Django app and can be switched on per gym.
2. Strong multi-tenant data isolation. Gym data is never visible to another gym.
3. Reliable and auditable financial transactions. Financial history is never
   silently overwritten; corrections are made by reversal.
4. Automated testing. No feature is done without tests.
5. Clear separation of business logic and user interfaces.
6. A migration path to separate front-end applications when commercially justified.

## Why

- The owner has Python skills, so the code can be read, checked and maintained.
- Django is mature and includes login, permissions and security defaults.
- One codebase is the fastest way for a very small team to reach paying gyms.
- Keeping rules in a service layer means the same tested code serves web pages
  now and an API later.

## Consequences

- The member pages will feel less app-like than a React app at first.
- Module boundaries must be enforced by discipline and by an automated import
  check (to be added in a later increment of Step 2).
