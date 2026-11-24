"""Tenant isolation, lock 2, for the guest rule and guest visits."""

from django.db import migrations

TABLES = ["attendance_guestrule", "attendance_guestvisit"]

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
        ("attendance", "0004_guests"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
