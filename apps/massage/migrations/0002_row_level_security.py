"""Tenant isolation, lock 2, for massage types, rules and bookings."""

from django.db import migrations

TABLES = ["massage_massagetype", "massage_massagerule", "massage_massagebooking"]

APPLY = "\n".join(
    f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON {table}
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""
    for table in TABLES
)

UNDO = "\n".join(
    f"""
DROP POLICY IF EXISTS gym_isolation ON {table};
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
    for table in TABLES
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("massage", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
