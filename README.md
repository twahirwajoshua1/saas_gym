# Gym Business Operating System

A multi-tenant SaaS platform for gyms in Rwanda: members, memberships, payments,
check-in and reporting first, with further modules added over time.

**Status:** First commercial release (Step 3) complete, pending a pilot with
real gyms. A gym can be registered by the platform team; its owner adds staff,
branches and packages; members are imported or registered; memberships are
sold, paid in full or in part, frozen or extended; receipts are printed;
members check in by search or QR card; the owner sees figures and renewals;
data can be exported. See "What is not built yet" below.

## How the project is organised

| Folder or file | What it is for |
|---|---|
| `manage.py` | The command you run to start the app, run database changes and more |
| `config/` | Project-wide settings and the main list of web addresses |
| `apps/` | Our own modules. Each of the nine applications gets its own folder here |
| `apps/core/` | The shared foundation, including the database lock that isolates gyms |
| `apps/people/` | One record per human being, identified by phone number |
| `apps/accounts/` | Login accounts: sign in with phone number and password |
| `apps/audit/` | The permanent audit log |
| `apps/modules/` | Which of the nine applications each gym has switched on |
| `apps/spreadsheets/` | Importing members from Excel or CSV, and exporting to Excel |
| `apps/team/` | Staff: adding people with a role and login, changing and removing them |
| `apps/memberapp/` | The member's card page (private link with QR code) and the card API |
| `apps/platformadmin/` | Pages for platform staff: gyms, owners, applications, suspension |
| `apps/sampledata/` | The `create_sample_gyms` command (demonstration data only) |
| `scripts/` | Backup and restore scripts |
| `apps/dashboard/` | The owner's figures and the renewals list (reads from the other modules) |
| `apps/attendance/` | Attendance and Access: checking members in |
| `apps/billing/` | Payments and Finance: invoices, payments, receipts and the ledger |
| `apps/members/` | Gym Operations: members, packages, memberships and prepaid visits |
| `apps/classes/` | Training and Classes: the timetable, trainers and one-to-one sessions |
| `apps/massage/` | Massage: the kinds a gym offers, who gives them and the bookings |
| `apps/corporate/` | Corporate Wellness: companies, contracts, visits, bills and reports |
| `apps/frontdesk/` | The desk's Home page, the Members list and the entrance tablets |
| `apps/gyms/` | Gyms (the tenants), branches, staff roles and the tenant isolation helper |
| `templates/` | Page layouts and form pieces shared by all modules |
| `static/` | The stylesheet (`css/app.css`) and third-party JavaScript (`vendor/`) |
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

   Run this line again after a `git pull` that changes `requirements.txt`.

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
   CREATE USER gymos WITH PASSWORD 'gymos_dev_password' CREATEDB CREATEROLE;
   CREATE DATABASE gymos OWNER gymos;
   ```

4. Copy `.env.example` to `.env` and replace the secret key with any long random text.

5. Check everything works:

   ```
   python manage.py check     # Django checks its own configuration
   python manage.py migrate   # creates or updates the database tables
   pytest                     # runs the automated tests
   python manage.py runserver # starts the app
   ```

   Then open http://127.0.0.1:8000/ in a browser. You should see
   "Application: running" and "Database: connected".

6. Create your own administrator account, then sign in at
   http://127.0.0.1:8000/accounts/login/ :

   ```
   python manage.py create_platform_admin --name "Your Name" --phone 0788123456
   ```

7. Register a gym owned by a registered person, then sign in again to enter it:

   ```
   python manage.py create_gym --name "Kigali Fit Club" --owner-phone 0788123456
   ```

## Everyday commands

| Command | What it does |
|---|---|
| `python manage.py runserver` | Start the app on your computer |
| `python manage.py migrate` | Apply new database changes after a `git pull` |
| `docker compose up -d` | Start the database |
| `python manage.py set_module --gym "Name"` | Show which applications a gym has |
| `python manage.py set_module --gym "Name" --module payments_finance` | Switch one on (add `--off` to switch off) |
| `pytest` | Run all automated tests |
| `ruff check .` | Look for common mistakes in the code |
| `lint-imports` | Check that modules only depend on each other in the allowed direction |
| `python manage.py unlock_sign_in --phone 0788123456` | Remove a sign-in lock early |
| `ruff format .` | Tidy the code layout |

## Sample data for demonstrations

On an empty development database:

```
python manage.py migrate
python manage.py create_sample_gyms
```

This creates five invented Rwandan gyms (with 1 to 5 branches), their
services (gym, sauna, massage...), five employers with corporate wellness
contracts (WASAC, REG, RTDA, REMA and MINAGRI, used by name for demonstrations
only; every person and visit is invented), an entrance tablet per branch, and
75 days of history including tablet check-ins, closed shifts and monthly
company bills, and prints every login. It takes several minutes. See
`docs/decisions/0017-sample-data.md`.

For quick demonstrations on your own computer you can also add short demo
names. Put `DEMO_LOGINS=True` in `.env` (it only works while `DEBUG=True`),
then run `python manage.py create_demo_logins`. Type the name in the phone
number box:

| Name | Password | Opens |
|---|---|---|
| gymowner | gymower | Owner of Umucyo Wellness Centre |
| receptionist | receptionist | Receptionist at Umucyo, Nyarutarama |
| member | member | A sample member's card (members have no login) |
| company | Company | WASAC's logistics officer |

Never switch this on for a real gym: anyone who knows these names gets in.

## Corporate wellness

Companies pay for their employees' visits to contracted gyms. See
`docs/decisions/0019-corporate-wellness.md` for how it works, and run
`python manage.py issue_corporate_bills` every evening (docs/operations.md).

## The front desk and the entrance tablet

Gym staff see one of two views. Owners and branch managers get **Today**
(figures and advice). Receptionists get the **Desk**: who is arriving, the few
people who need them, search, and their own shift's cash.

To set up an entrance tablet (any tablet or old phone with a front camera and a
browser):

1. As the owner, open **Settings → Entrance tablets**, add one for a branch and
   note the 6-digit code (valid 15 minutes).
2. On the tablet, open `/tablet/` on your gym's address and type the code.
3. Leave it on a stand at the entrance. Members and company staff scan their
   phone card (or type their PIN); only problems go to the desk.

See `docs/decisions/0020-front-desk-tablet-streaks-league.md`.

## Backups

See `docs/operations.md` for nightly backups, restoring, and the restore drill.

## What is not built yet

- Live mobile-money collection (payments are recorded by staff), SMS and
  WhatsApp messages, password reset by SMS.
- Leads, trials, campaigns, loyalty (Sales and Growth, Retention and Loyalty).
- Trainers' client lists, classes and bookings; inventory.
- Corporate wellness extras: employee self-registration link, photos, SMS to
  employees, EBM numbers on company bills (see decision 0019).
- Platform subscriptions and billing gyms; logged support access.
- Kinyarwanda and French translations (screens are prepared for them).
- Deployment to a server (hosting choice and data location are open questions).

## Where to read more

- Architecture and development plan: the "Gym Business Operating System:
  Architecture and Development Plan" document.
- Decisions: `docs/decisions/`.
