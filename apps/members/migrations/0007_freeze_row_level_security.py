"""Tenant isolation, lock 2, for membership freezes."""

from django.db import migrations

APPLY = """
ALTER TABLE members_membershipfreeze ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON members_membershipfreeze
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON members_membershipfreeze;
ALTER TABLE members_membershipfreeze DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("members", "0006_membership_freeze"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
