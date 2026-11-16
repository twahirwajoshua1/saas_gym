"""
Gym staff may read their own gym's module switches but never change them.
Only the platform side (migrations, commands, the platform admin) can write.
"""

from django.db import migrations

APPLY = """
ALTER TABLE modules_moduleactivation ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON modules_moduleactivation
    FOR SELECT TO gymos_app
    USING (gym_id = app_current_gym_id());
REVOKE INSERT, UPDATE, DELETE ON modules_moduleactivation FROM gymos_app;
"""

UNDO = """
GRANT INSERT, UPDATE, DELETE ON modules_moduleactivation TO gymos_app;
DROP POLICY IF EXISTS gym_isolation ON modules_moduleactivation;
ALTER TABLE modules_moduleactivation DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("modules", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
