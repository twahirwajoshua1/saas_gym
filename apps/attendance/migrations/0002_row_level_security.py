"""Tenant isolation, lock 2, for check-ins."""

from django.db import migrations

APPLY = """
ALTER TABLE attendance_checkin ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON attendance_checkin
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON attendance_checkin;
ALTER TABLE attendance_checkin DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("attendance", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
