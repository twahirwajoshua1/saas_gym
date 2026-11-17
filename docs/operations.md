# Running the platform: backups and restores

## What to back up

Everything is in the PostgreSQL database. Uploaded spreadsheets are not kept
after an import, and there are no other files to save. The `.env` file holds
the secret key and database password: keep a copy of it somewhere safe and
separate (a password manager), never in Git.

## Backups on the server (Linux)

`scripts/backup.sh` writes a compressed backup file named with the date and
time, checks the file can be read, and deletes backups older than 30 days.

Run it every night at 02:15 with cron (`crontab -e`):

```
15 2 * * * cd /srv/gym-business-os && BACKUP_DIR=/var/backups/gymos scripts/backup.sh >> /var/log/gymos-backup.log 2>&1
```

Then copy `/var/backups/gymos` to a second place every day (another server or
storage service). A backup kept only on the same server is lost with it.
Check where backups may be stored under Rwanda's data protection law before
choosing a place abroad.

## Backups on your own computer (Docker)

In `C:\saas_gym`, with the database running:

```
docker compose exec -T db pg_dump -U gymos --format=custom gymos > backup.dump
```

## Restoring

Restore into a new, empty database, never over the live one:

```
scripts/restore.sh /var/backups/gymos/gymos-2026-11-17-021500.dump postgres://gymos:PASSWORD@localhost:5432/gymos_restored
```

The script refuses a database that is not empty. It first creates the
restricted role the web application uses (`gymos_app`), because that role
belongs to the database server and is not inside a backup. It then restores
and prints a few counts to compare with what you expect.

After checking the restored copy, point `DATABASE_URL` in `.env` at it and
restart the application.

## Practise a restore

A backup is only proven by restoring it. Every three months, restore the
latest backup into a spare database and check it, as was done when these
scripts were written (2026-11-17):

- every table had exactly the same number of rows as the original;
- the web role saw no members until a gym was chosen (tenant isolation held);
- editing a payment was refused (permanent records held);
- the card lookup function was present;
- `python manage.py migrate --check` reported nothing missing.

## Testing the camera scanner on a phone

Phone browsers only open the camera on `https://` pages. To reach your
computer's development server from a phone over HTTPS, use a temporary tunnel:

1. Install `cloudflared` (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Add to `.env`:
   ```
   ALLOWED_HOSTS=localhost,127.0.0.1,.trycloudflare.com
   CSRF_TRUSTED_ORIGINS=https://*.trycloudflare.com
   BEHIND_HTTPS_PROXY=True
   ```
3. Start the app (`python manage.py runserver`), then in a second terminal:
   `cloudflared tunnel --url http://localhost:8000`
4. Open the `https://….trycloudflare.com` address it prints on the phone.
5. Stop the tunnel (Ctrl+C) when finished. While it runs, anyone with the
   address can reach your development server.

On a laptop, the camera works at `http://localhost:8000` with no tunnel.

## Nightly job: company bills

Run this every evening, for example at 23:30 Kigali time:

```
python manage.py issue_corporate_bills
```

On the last day of a month it issues that month's bill for every company
contract; on later days it catches up on anything missed. Running it twice
does no harm.
