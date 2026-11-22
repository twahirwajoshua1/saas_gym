"""
The monthly league of companies, answered by the database without exposing
anyone's details: for each company taking part, its name, how many employees
are on its list, and how many of them came at least `min_visits` times.
"""

from django.db import migrations

APPLY = """
CREATE FUNCTION corporate_league(month_start date, month_end date, min_visits integer)
RETURNS TABLE(company_id bigint, name text, listed integer, active integer)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$
    SELECT c.id, c.name::text,
           (SELECT count(*) FROM corporate_employee e WHERE e.company_id = c.id AND e.is_active)::integer,
           (SELECT count(*) FROM corporate_employee e
             WHERE e.company_id = c.id AND e.is_active
               AND (SELECT count(*) FROM corporate_corporatevisit v
                     WHERE v.employee_id = e.id
                       AND v.checked_in_on BETWEEN month_start AND month_end) >= min_visits)::integer
    FROM corporate_company c
    WHERE c.is_active AND c.in_league
$$;
REVOKE ALL ON FUNCTION corporate_league(date, date, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION corporate_league(date, date, integer) TO gymos_app;
"""

UNDO = "DROP FUNCTION IF EXISTS corporate_league(date, date, integer);"


class Migration(migrations.Migration):
    dependencies = [("corporate", "0004_league_and_tablet")]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
