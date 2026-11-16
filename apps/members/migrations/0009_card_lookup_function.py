"""
A database function that finds the gym of a card link.

Used when someone who is not signed in opens a member card. It runs with the
rights of the table owner (SECURITY DEFINER), so it can see past tenant
isolation, but it only ever returns one number: the gym of the member whose
card token matches exactly.
"""

from django.db import migrations

APPLY = """
CREATE FUNCTION app_gym_for_card(token text) RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT gym_id FROM members_member WHERE card_token = token AND token <> '' $$;
REVOKE ALL ON FUNCTION app_gym_for_card(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_gym_for_card(text) TO gymos_app;
"""

UNDO = "DROP FUNCTION IF EXISTS app_gym_for_card(text);"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("members", "0008_member_card_token"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
