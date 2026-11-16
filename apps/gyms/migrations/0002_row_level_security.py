"""
Tenant isolation, lock 2: row-level security policies on gym-owned tables.

Every table that inherits GymOwnedModel must have a policy here. A test fails
if a new gym-owned table is added without one.
"""

from django.db import migrations

ENABLE = """
ALTER TABLE gyms_branch ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON gyms_branch
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());

-- Staff records are visible for the current gym, and a person can always see
-- their own records (needed to list the gyms they work at before choosing one).
-- New records can only be written for the current gym.
ALTER TABLE gyms_staffassignment ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON gyms_staffassignment
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id() OR person_id = app_current_person_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

DISABLE = """
DROP POLICY IF EXISTS gym_isolation ON gyms_staffassignment;
ALTER TABLE gyms_staffassignment DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS gym_isolation ON gyms_branch;
ALTER TABLE gyms_branch DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("gyms", "0001_initial"),
    ]

    operations = [migrations.RunSQL(ENABLE, reverse_sql=DISABLE)]
