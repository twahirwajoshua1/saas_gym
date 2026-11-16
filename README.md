# Gym Business Operating System

A multi-tenant SaaS platform for gyms in Rwanda: members, memberships, payments,
check-in and reporting first, with further modules added over time.

**Status:** Step 2 (Foundation), increment 1. The project runs and connects to
PostgreSQL. There are no members, logins or gyms yet.

## How the project is organised

| Folder or file | What it is for |
|---|---|
| `manage.py` | The command you run to start the app, run database changes and more |
| `config/` | Project-wide settings and the main list of web addresses |
| `apps/` | Our own modules. Each of the nine applications gets its own folder here |
| `apps/core/` | The shared foundation used by every other module |
| `tests/` | Automated tests |
| `docs/decisions/` | A record of important architecture decisions and why we made them |
| `.env` | Private settings for your machine (passwords, secret key). Never saved to Git |
| `.env.example` | A template showing which settings `.env` needs |
| `requirements.txt` | The list of Python packages the project uses |
| `pyproject.toml` | Settings for the test runner (pytest) and the code checker (Ruff) |

Inside each module:

| File | What goes in it |
|---|---|
| `models.py` | The database tables |
| `services.py` | The business rules. This is the only place rules live |
| `views.py` | Screens. They call services and show the result; no rules here |
| `urls.py` | The web addresses of this module's screens |
| `templates/` | The HTML pages |

## Setting up on your own computer

You need Python 3.12 or newer, Git, and either Docker or PostgreSQL 16.

1. Get the code and enter the folder:

   ```
   git clone <repository address>
   cd gym-business-os
   ```

2. Create a private Python environment and install the packages:

   ```
   python -m venv .venv
   source .venv/bin/activate        # on Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Start the database. The easiest way is Docker, which runs PostgreSQL
   without installing it:

   ```
   docker compose up -d
   ```

   This creates a database called `gymos` with the development user and
   password already set in `.env.example`.

   If you have PostgreSQL installed instead, run this inside `psql` as the
   administrator:

   ```
   CREATE USER gymos WITH PASSWORD 'gymos_dev_password' CREATEDB;
   CREATE DATABASE gymos OWNER gymos;
   ```

4. Copy `.env.example` to `.env` and replace the secret key with any long random text.

5. Check everything works:

   ```
   python manage.py check     # Django checks its own configuration
   pytest                     # runs the automated tests
   python manage.py runserver # starts the app
   ```

   Then open http://127.0.0.1:8000/ in a browser. You should see
   "Application: running" and "Database: connected".

Do not run `python manage.py migrate` yet. See decision 0002 for the reason.

## Everyday commands

| Command | What it does |
|---|---|
| `python manage.py runserver` | Start the app on your computer |
| `pytest` | Run all automated tests |
| `ruff check .` | Look for common mistakes in the code |
| `ruff format .` | Tidy the code layout |

## Where to read more

- Architecture and development plan: the "Gym Business Operating System:
  Architecture and Development Plan" document.
- Decisions: `docs/decisions/`.
