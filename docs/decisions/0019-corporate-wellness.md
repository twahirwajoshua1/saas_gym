# 0019: Corporate wellness

Date: 2026-11-17
Status: Accepted (first version, for the founder's review)

## The problem it replaces

Today a company gives employees a list of contracted gyms. At the gym each
employee signs a paper list per service (name, phone, department, employee
number, date, hour, signature). At month end the gym sends the lists to the
company, which pays by bank transfer. Company cards get lent, the company's
Excel list is often out of date, and the company has no way to check the lists.

## Decisions

- **Services first.** A gym lists the single sessions it sells (Gym, Sauna,
  Massage, Swimming, Aerobics...) with a member price. Members can buy them
  per use. Company contracts price the same services.
- **Companies are platform-level**, created by platform staff with a
  logistics officer (who gets a login) and a short company code (CO-XXXXXX).
  A gym proposes a contract by typing the code; the code only reveals the
  company's name. The company approves or declines. Approved terms are fixed;
  to change prices, end the contract and make a new one.
- **Prices per service per branch**, because branches price differently.
- **One shared employee list, kept by the company.** Added one by one or from
  their usual Excel file (with a preview of new, changed and missing people
  before saving). Gyms see changes at once. Removed employees cannot check in
  from that moment; their history stays.
- **Proving it is the employee, without photos** (photos may come later):
  - the card link opens on one phone only (the first one; the officer can
    allow a new phone);
  - the QR carries a code that changes every 30 seconds; the desk accepts
    about two minutes' worth, so screenshots cannot be lent;
  - employees without a smartphone use a printed card plus a 4-digit PIN
    (5 wrong PINs lock it for 15 minutes at that gym);
  - the employee confirms each visit on their phone, which replaces the
    signature; the company sees which visits were confirmed.
- **Warnings visible to both sides**: the same employee at two places within
  an hour, or a visit the company questions. A visit with an open warning
  stays off the bill until the company accepts it or the gym takes it off.
- **Bills**: one per gym per company per month, issued on the last evening
  (`python manage.py issue_corporate_bills` every evening; gyms can also
  issue once the last day has come). Lines are copies, grouped by service
  like the paper lists, and permanent. Visits held back by a warning, and
  credits for wrongly billed lines (minus lines), go on the next bill.
  Payment terms (15 to 45 days) come from the contract; payments are bank
  transfers with a reference, reversible by the owner, never edited.
- **Security**: the database enforces two kinds of tenant: a gym and a
  company (`app.current_company_id`). Gyms see employees only of companies
  with an approved contract with them (until 90 days after it ends). Three
  narrow SECURITY DEFINER functions answer single questions across tenants.

## Not yet

Employee self-registration link; photos; SMS to employees (needs a provider);
EBM invoice numbers on bills (waiting for the founder's information);
fees the platform charges companies and gyms for this feature.
