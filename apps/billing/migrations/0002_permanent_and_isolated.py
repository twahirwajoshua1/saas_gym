"""
Protects the money records in the database itself.

- Private: every billing table is covered by tenant isolation, lock 2.
- Permanent: invoices, invoice lines, payments, allocations, reversals and
  ledger entries can be added but never changed, deleted or emptied, by any
  database user. Corrections are made by adding a new record.

The numbering table (billing_sequence) is the one exception: it has to be
updated each time a number is used.
"""

from django.db import migrations

ALL_TABLES = [
    "billing_sequence",
    "billing_invoice",
    "billing_invoiceline",
    "billing_payment",
    "billing_allocation",
    "billing_reversal",
    "billing_ledgerentry",
]
PERMANENT_TABLES = [table for table in ALL_TABLES if table != "billing_sequence"]

APPLY = """
CREATE FUNCTION billing_records_are_permanent() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'Money records are permanent and cannot be changed or deleted (table %).',
        TG_TABLE_NAME;
END
$$;
"""
for table in ALL_TABLES:
    APPLY += f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON {table}
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""
for table in PERMANENT_TABLES:
    APPLY += f"""
CREATE TRIGGER no_change BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION billing_records_are_permanent();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION billing_records_are_permanent();
REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM gymos_app;
"""

UNDO = ""
for table in PERMANENT_TABLES:
    UNDO += f"""
GRANT UPDATE, DELETE ON {table} TO gymos_app;
DROP TRIGGER IF EXISTS no_truncate ON {table};
DROP TRIGGER IF EXISTS no_change ON {table};
"""
for table in ALL_TABLES:
    UNDO += f"""
DROP POLICY IF EXISTS gym_isolation ON {table};
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
UNDO += "DROP FUNCTION IF EXISTS billing_records_are_permanent();"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("billing", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
