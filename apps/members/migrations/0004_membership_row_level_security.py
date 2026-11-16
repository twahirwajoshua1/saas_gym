"""Tenant isolation, lock 2, for memberships."""

from django.db import migrations

APPLY = """
ALTER TABLE members_membership ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON members_membership
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON members_membership;
ALTER TABLE members_membership DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("members", "0003_membership"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
