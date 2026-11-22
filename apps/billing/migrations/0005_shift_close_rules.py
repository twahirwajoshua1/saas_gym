"""Shift closes: visible only inside their gym, and permanent."""

from django.db import migrations

APPLY = """
ALTER TABLE billing_shiftclose ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON billing_shiftclose FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id()) WITH CHECK (gym_id = app_current_gym_id());
CREATE TRIGGER no_change BEFORE UPDATE OR DELETE ON billing_shiftclose
    FOR EACH ROW EXECUTE FUNCTION billing_records_are_permanent();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON billing_shiftclose
    FOR EACH STATEMENT EXECUTE FUNCTION billing_records_are_permanent();
REVOKE UPDATE, DELETE, TRUNCATE ON billing_shiftclose FROM gymos_app;
"""

UNDO = """
GRANT UPDATE, DELETE ON billing_shiftclose TO gymos_app;
DROP TRIGGER IF EXISTS no_truncate ON billing_shiftclose;
DROP TRIGGER IF EXISTS no_change ON billing_shiftclose;
DROP POLICY IF EXISTS gym_isolation ON billing_shiftclose;
ALTER TABLE billing_shiftclose DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("billing", "0004_shift_close")]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
