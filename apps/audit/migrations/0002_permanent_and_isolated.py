"""
Makes the audit log permanent and private.

- Permanent: the database refuses to change, delete or empty the table, for
  every user including the one that runs migrations.
- Private: the restricted web role may add entries and read its own gym's
  entries, and nothing else (tenant isolation, lock 2).
"""

from django.db import migrations

APPLY = """
CREATE FUNCTION audit_entries_are_permanent() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'Audit entries are permanent and cannot be changed or deleted.';
END
$$;

CREATE TRIGGER audit_entries_no_change
    BEFORE UPDATE OR DELETE ON audit_auditentry
    FOR EACH ROW EXECUTE FUNCTION audit_entries_are_permanent();

CREATE TRIGGER audit_entries_no_truncate
    BEFORE TRUNCATE ON audit_auditentry
    FOR EACH STATEMENT EXECUTE FUNCTION audit_entries_are_permanent();

REVOKE UPDATE, DELETE, TRUNCATE ON audit_auditentry FROM gymos_app;

ALTER TABLE audit_auditentry ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON audit_auditentry
    FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id())
    WITH CHECK (gym_id = app_current_gym_id());
"""

UNDO = """
DROP POLICY IF EXISTS gym_isolation ON audit_auditentry;
ALTER TABLE audit_auditentry DISABLE ROW LEVEL SECURITY;
GRANT UPDATE, DELETE ON audit_auditentry TO gymos_app;
DROP TRIGGER IF EXISTS audit_entries_no_truncate ON audit_auditentry;
DROP TRIGGER IF EXISTS audit_entries_no_change ON audit_auditentry;
DROP FUNCTION IF EXISTS audit_entries_are_permanent();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("audit", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
