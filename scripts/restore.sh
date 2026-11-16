#!/usr/bin/env bash
# Restores a backup into an EMPTY database.
#
#   scripts/restore.sh backups/gymos-2026-11-17-020000.dump postgres://gymos:...@host:5432/gymos_restored
#
# The restricted role the web application uses (gymos_app) belongs to the
# database server, not to the database, so it is not inside the backup. This
# script creates it first if the server does not have it yet, exactly as the
# first migration does. The user in the address must be the one that will run
# migrations (the owner of the tables).
set -euo pipefail

backup="${1:?Give the backup file}"
target="${2:?Give the address of an empty database}"

existing=$(psql "$target" -At -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
if [[ "$existing" != "0" ]]; then
  echo "The target database is not empty ($existing tables). Nothing was changed." >&2
  exit 1
fi

psql "$target" -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gymos_app') THEN
        CREATE ROLE gymos_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;
GRANT gymos_app TO CURRENT_USER;
SQL

pg_restore --no-owner --exit-on-error --dbname="$target" "$backup"

echo "Restored. Checks:"
psql "$target" -At <<'SQL'
SELECT 'gyms: ' || count(*) FROM gyms_gym;
SELECT 'members: ' || count(*) FROM members_member;
SELECT 'payments: ' || count(*) FROM billing_payment;
SELECT 'protected tables: ' || count(*) FROM pg_class WHERE relrowsecurity;
SELECT 'permanent-record rules: ' || count(*) FROM pg_trigger WHERE tgname IN ('no_change', 'audit_entries_no_change');
SQL
