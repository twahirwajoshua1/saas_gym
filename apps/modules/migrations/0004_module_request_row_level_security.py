"""
A gym may ask for an application, and may see and withdraw only its own asks.

This is the opposite way round from `ModuleActivation`: there the gym reads and
the platform writes, because switching an application on is the platform's
decision. Asking is the gym's own, so the gym writes here.
"""

from django.db import migrations

APPLY = """
ALTER TABLE modules_modulerequest ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON modules_modulerequest
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON modules_modulerequest;
ALTER TABLE modules_modulerequest DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("modules", "0003_modulerequest"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
