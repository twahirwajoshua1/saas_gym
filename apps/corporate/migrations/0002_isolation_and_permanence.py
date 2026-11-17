"""
Who may see and change corporate records, enforced by the database.

Two kinds of "who" meet here:
  * a gym (app.current_gym_id), as for every other gym table, and
  * a company (app.current_company_id), set for the company's logistics
    officer and for an employee opening their own card.

Rules:
  * A gym sees its own contracts, visits, warnings and bills, and the
    employees of companies that have approved a contract with it.
  * A company sees its own employees, and its contracts, visits, warnings
    and bills at every gym.
  * Only the gym records visits and bills; only the company manages its
    employee list. Both can raise and settle warnings.
  * Bills, bill lines, payments, reversals, credits and the company log are
    permanent.

Three narrow look-up functions run with the owner's rights (SECURITY
DEFINER) and each return one fact only: the company for a contract code, the
company for an employee card, and whether an employee was checked in at
another place within the last hour.
"""

from django.db import migrations

GYM = "gym_id = app_current_gym_id()"
COMPANY = "company_id = app_current_company_id()"
EITHER = f"({GYM} OR {COMPANY})"

# table: (who may read, who may add, who may change, permanent?)
SHARED_TABLES = {
    "corporate_contract": (EITHER, GYM, EITHER, False),
    "corporate_contractprice": (EITHER, GYM, GYM, False),
    "corporate_corporatevisit": (EITHER, GYM, EITHER, False),
    "corporate_visitactivity": (EITHER, GYM, GYM, False),
    "corporate_flag": (EITHER, EITHER, EITHER, False),
    "corporate_corporatebill": (EITHER, GYM, None, True),
    "corporate_billline": (EITHER, GYM, None, True),
    "corporate_credit": (EITHER, GYM, None, True),
    "corporate_billpayment": (EITHER, GYM, None, True),
    "corporate_paymentreversal": (EITHER, GYM, None, True),
}
GYM_ONLY_TABLES = ["corporate_corporatesequence", "corporate_pinattempt"]
PERMANENT = [t for t, rules in SHARED_TABLES.items() if rules[3]] + ["corporate_companylogentry"]

APPLY = """
CREATE OR REPLACE FUNCTION app_current_company_id() RETURNS bigint
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.current_company_id', true), '')::bigint $$;

CREATE FUNCTION corporate_records_are_permanent() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'Corporate billing records are permanent and cannot be changed or deleted (table %).',
        TG_TABLE_NAME;
END
$$;

-- Companies: visible to their own officers and employees, and to gyms they
-- have a contract with. Only the platform creates them.
ALTER TABLE corporate_company ENABLE ROW LEVEL SECURITY;
CREATE POLICY company_read ON corporate_company FOR SELECT TO gymos_app USING (
    id = app_current_company_id()
    OR EXISTS (SELECT 1 FROM corporate_contract c
               WHERE c.company_id = corporate_company.id AND c.gym_id = app_current_gym_id())
    OR EXISTS (SELECT 1 FROM corporate_companyofficer o
               WHERE o.company_id = corporate_company.id
                 AND o.person_id = app_current_person_id() AND o.is_active)
);

ALTER TABLE corporate_companyofficer ENABLE ROW LEVEL SECURITY;
CREATE POLICY officer_read ON corporate_companyofficer FOR SELECT TO gymos_app USING (
    company_id = app_current_company_id() OR person_id = app_current_person_id()
);

-- Employees: the company manages them; gyms with an approved contract see them.
ALTER TABLE corporate_employee ENABLE ROW LEVEL SECURITY;
CREATE POLICY employee_read ON corporate_employee FOR SELECT TO gymos_app USING (
    company_id = app_current_company_id()
    OR EXISTS (SELECT 1 FROM corporate_contract c
               WHERE c.company_id = corporate_employee.company_id
                 AND c.gym_id = app_current_gym_id() AND c.status = 'approved'
                 -- kept visible for 90 days after the contract ends, for the last bills
                 AND current_date <= COALESCE(c.ended_on, c.end_date) + 90)
);
CREATE POLICY employee_add ON corporate_employee FOR INSERT TO gymos_app
    WITH CHECK (company_id = app_current_company_id());
CREATE POLICY employee_change ON corporate_employee FOR UPDATE TO gymos_app
    USING (company_id = app_current_company_id())
    WITH CHECK (company_id = app_current_company_id());

ALTER TABLE corporate_companylogentry ENABLE ROW LEVEL SECURITY;
CREATE POLICY log_read ON corporate_companylogentry FOR SELECT TO gymos_app
    USING (company_id = app_current_company_id());
CREATE POLICY log_add ON corporate_companylogentry FOR INSERT TO gymos_app
    WITH CHECK (company_id = app_current_company_id());
"""

for table, (read, add, change, _permanent) in SHARED_TABLES.items():
    APPLY += f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON {table} FOR SELECT TO gymos_app USING {read};
CREATE POLICY add_rows ON {table} FOR INSERT TO gymos_app WITH CHECK ({add});
"""
    if change:
        APPLY += f"""
CREATE POLICY change_rows ON {table} FOR UPDATE TO gymos_app USING ({change}) WITH CHECK ({change});
"""
    if table == "corporate_contractprice":
        APPLY += f"""
CREATE POLICY remove_rows ON {table} FOR DELETE TO gymos_app USING ({GYM});
"""

for table in GYM_ONLY_TABLES:
    APPLY += f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
CREATE POLICY gym_isolation ON {table} FOR ALL TO gymos_app USING ({GYM}) WITH CHECK ({GYM});
"""

for table in PERMANENT:
    APPLY += f"""
CREATE TRIGGER no_change BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION corporate_records_are_permanent();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION corporate_records_are_permanent();
REVOKE UPDATE, DELETE, TRUNCATE ON {table} FROM gymos_app;
"""

APPLY += """
-- The gym typed a company's contract code: which company is it?
CREATE FUNCTION corporate_company_for_code(company_code text) RETURNS TABLE(id bigint, name text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT id, name::text FROM corporate_company
          WHERE code = upper(company_code) AND is_active AND company_code <> '' $$;
REVOKE ALL ON FUNCTION corporate_company_for_code(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION corporate_company_for_code(text) TO gymos_app;

-- Someone opened (or the desk scanned) an employee card: which company?
CREATE FUNCTION corporate_company_for_card(token text) RETURNS bigint
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT company_id FROM corporate_employee WHERE card_token = token AND token <> '' $$;
REVOKE ALL ON FUNCTION corporate_company_for_card(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION corporate_company_for_card(text) TO gymos_app;

-- Was this employee checked in somewhere else (another gym or branch) since a time?
CREATE FUNCTION corporate_visited_elsewhere(
    employee bigint, gym bigint, branch bigint, since timestamptz
) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public
    AS $$ SELECT EXISTS (
        SELECT 1 FROM corporate_corporatevisit v
        WHERE v.employee_id = employee AND v.checked_in_at >= since
          AND (v.gym_id <> gym OR v.branch_id <> branch)
    ) $$;
REVOKE ALL ON FUNCTION corporate_visited_elsewhere(bigint, bigint, bigint, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION corporate_visited_elsewhere(bigint, bigint, bigint, timestamptz) TO gymos_app;
"""

UNDO = """
DROP FUNCTION IF EXISTS corporate_visited_elsewhere(bigint, bigint, bigint, timestamptz);
DROP FUNCTION IF EXISTS corporate_company_for_card(text);
DROP FUNCTION IF EXISTS corporate_company_for_code(text);
"""
for table in PERMANENT:
    UNDO += f"""
GRANT UPDATE, DELETE ON {table} TO gymos_app;
DROP TRIGGER IF EXISTS no_truncate ON {table};
DROP TRIGGER IF EXISTS no_change ON {table};
"""
for table in [*SHARED_TABLES, *GYM_ONLY_TABLES]:
    UNDO += f"""
DROP POLICY IF EXISTS gym_isolation ON {table};
DROP POLICY IF EXISTS add_rows ON {table};
DROP POLICY IF EXISTS change_rows ON {table};
DROP POLICY IF EXISTS remove_rows ON {table};
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
UNDO += """
DROP POLICY IF EXISTS log_read ON corporate_companylogentry;
DROP POLICY IF EXISTS log_add ON corporate_companylogentry;
ALTER TABLE corporate_companylogentry DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS employee_read ON corporate_employee;
DROP POLICY IF EXISTS employee_add ON corporate_employee;
DROP POLICY IF EXISTS employee_change ON corporate_employee;
ALTER TABLE corporate_employee DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS officer_read ON corporate_companyofficer;
ALTER TABLE corporate_companyofficer DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS company_read ON corporate_company;
ALTER TABLE corporate_company DISABLE ROW LEVEL SECURITY;
DROP FUNCTION IF EXISTS corporate_records_are_permanent();
DROP FUNCTION IF EXISTS app_current_company_id();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_database_role"),
        ("corporate", "0001_initial"),
    ]

    operations = [migrations.RunSQL(APPLY, reverse_sql=UNDO)]
