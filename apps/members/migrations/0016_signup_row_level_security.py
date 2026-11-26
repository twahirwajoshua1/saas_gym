"""Tenant isolation, lock 2, for sign-ups from the gym's own page."""

from django.db import migrations

APPLY = """
ALTER TABLE members_signup ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON members_signup
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON members_signup;
ALTER TABLE members_signup DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("members", "0015_signup"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
