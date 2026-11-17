"""Tenant isolation, lock 2, for the services a gym sells."""

from django.db import migrations

ENABLE = """
ALTER TABLE members_service ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON members_service
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

DISABLE = """
DROP POLICY IF EXISTS gym_isolation ON members_service;
ALTER TABLE members_service DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("members", "0010_service"),
    ]

    operations = [migrations.RunSQL(ENABLE, reverse_sql=DISABLE)]
