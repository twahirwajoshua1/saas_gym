# 0002: Project setup choices

Date: 2026-11-16
Status: Accepted

## Decisions

- **Django 5.2**, the long-term support release. It receives security fixes
  for longer than the newest release, which suits a commercial product.
- **PostgreSQL in every environment**, including development and tests. We do
  not use SQLite, because tenant isolation will rely on PostgreSQL features.
- **Settings come from a `.env` file** that is never saved to Git, so
  passwords and the secret key stay out of the code history.
- **Our modules live in `apps/`**, one folder per module, each with
  `models.py`, `services.py`, `views.py`, `urls.py` and `templates/`.
- **pytest** runs the tests and **Ruff** checks and formats the code.
- **Time zone is Africa/Kigali**; times are stored in UTC in the database.

## Database migrations are deliberately not run yet

Django recommends deciding on a custom user model before the first database
migration, because changing it afterwards is difficult. Our users will log in
mainly by phone number, which needs a custom user model. That model is built
in increment 2 (see decision 0003). Until that increment we did not run
`python manage.py migrate` on the development database.

The automated tests are not affected: pytest creates its own temporary
database each time and removes it afterwards.
