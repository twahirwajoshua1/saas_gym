"""Tenant isolation, lock 2, for what a member did on a visit."""

from django.db import migrations

APPLY = """
ALTER TABLE attendance_checkinactivity ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON attendance_checkinactivity
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON attendance_checkinactivity;
ALTER TABLE attendance_checkinactivity DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("attendance", "0006_checkinactivity"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
