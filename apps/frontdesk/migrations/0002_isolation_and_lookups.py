"""
Tablets and scan problems belong to one gym (row-level security).

The tablet itself has no sign-in, so two narrow look-ups run with the owner's
rights (SECURITY DEFINER) and each answer one question: which tablet (and gym)
has this connection code, and which has this secret.
"""

from django.db import migrations

TABLES = ["frontdesk_entrancetablet", "frontdesk_scanproblem"]

APPLY = (
    "".join(
        f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON {t} FOR ALL TO gymos_app
    USING (gym_id = app_current_gym_id()) WITH CHECK (gym_id = app_current_gym_id());
"""
        for t in TABLES
    )
    + """
CREATE FUNCTION frontdesk_tablet_for_code(code text) RETURNS TABLE(id bigint, gym_id bigint)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT t.id, t.gym_id FROM frontdesk_entrancetablet t
          WHERE t.is_active AND t.pairing_code = code AND code <> ''
            AND t.pairing_expires_at > now() $$;
REVOKE ALL ON FUNCTION frontdesk_tablet_for_code(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION frontdesk_tablet_for_code(text) TO gymos_app;

CREATE FUNCTION frontdesk_tablet_for_secret(hash text) RETURNS TABLE(id bigint, gym_id bigint)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT t.id, t.gym_id FROM frontdesk_entrancetablet t
          WHERE t.is_active AND t.secret_hash = hash AND hash <> '' $$;
REVOKE ALL ON FUNCTION frontdesk_tablet_for_secret(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION frontdesk_tablet_for_secret(text) TO gymos_app;
"""
)

UNDO = """
DROP FUNCTION IF EXISTS frontdesk_tablet_for_secret(text);
DROP FUNCTION IF EXISTS frontdesk_tablet_for_code(text);
""" + "".join(
    f"""
DROP POLICY IF EXISTS gym_isolation ON {t};
ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;
"""
    for t in TABLES
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("frontdesk", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
