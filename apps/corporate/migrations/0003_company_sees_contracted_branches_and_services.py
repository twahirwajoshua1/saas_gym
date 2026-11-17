"""
A company may read the branch and service names of gyms it has a contract
with, so its visits, contracts and bills can show them. Read only.
"""

from django.db import migrations

APPLY = """
CREATE POLICY company_read ON gyms_branch FOR SELECT TO gymos_app USING (
    EXISTS (SELECT 1 FROM corporate_contract c
            WHERE c.gym_id = gyms_branch.gym_id AND c.company_id = app_current_company_id())
);
CREATE POLICY company_read ON members_service FOR SELECT TO gymos_app USING (
    EXISTS (SELECT 1 FROM corporate_contract c
            WHERE c.gym_id = members_service.gym_id AND c.company_id = app_current_company_id())
);
"""

UNDO = """
DROP POLICY IF EXISTS company_read ON gyms_branch;
DROP POLICY IF EXISTS company_read ON members_service;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("corporate", "0002_isolation_and_permanence"),
        ("gyms", "0002_row_level_security"),
        ("members", "0011_service_row_level_security"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
