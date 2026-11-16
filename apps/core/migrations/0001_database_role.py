"""
Creates the restricted database role used for web requests, and the helper
functions that row-level security policies use.

The role cannot log in. The normal database user switches to it with
SET ROLE, which is why no extra password is needed.
"""

from django.db import migrations

CREATE_ROLE_AND_GRANTS = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gymos_app') THEN
        CREATE ROLE gymos_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

-- Lets the user that runs migrations switch to the role.
GRANT gymos_app TO CURRENT_USER;

GRANT USAGE ON SCHEMA public TO gymos_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gymos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gymos_app;

-- Tables created by later migrations get the same permissions automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gymos_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO gymos_app;

-- The web application has no business touching the migration history.
REVOKE ALL ON TABLE django_migrations FROM gymos_app;

-- Who is asking, and which gym are they acting for? Empty means "nobody" and
-- therefore matches no rows at all.
CREATE OR REPLACE FUNCTION app_current_gym_id() RETURNS bigint
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.current_gym_id', true), '')::bigint $$;

CREATE OR REPLACE FUNCTION app_current_person_id() RETURNS bigint
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.current_person_id', true), '')::bigint $$;
"""


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.RunSQL(
            CREATE_ROLE_AND_GRANTS,
            # The role may be shared with other databases, so it is never dropped.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
