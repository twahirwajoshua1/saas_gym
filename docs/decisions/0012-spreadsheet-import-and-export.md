# 0012: Importing members from spreadsheets, and exporting to Excel

Date: 2026-11-16
Status: Accepted

## Import (`apps/spreadsheets`)

- **Two steps: check, then confirm.** The upload is read and every row is
  checked and labelled (ready, already a member, or with problems). Nothing is
  saved. Confirming saves the ready rows.
- **All or nothing.** Confirming saves every ready row in one transaction. If
  anything unexpected fails, nothing is saved.
- **The same rules as the desk.** Rows are checked with the members module's
  own rules (`check_registration`, `register_member`), so an imported member is
  held to the same standard as one typed in.
- **Rows are checked again at confirm**, because a member may have been
  registered at the desk after the preview. Those rows are then skipped.
- **Pending rows are kept in the person's session**, tied to the gym and a
  one-time token. The session copy is removed when Confirm is pressed, so
  pressing it twice imports once, and a preview made in one gym cannot be
  confirmed in another.
- **Current memberships can be brought in** with Package and Valid until
  columns. Such a membership is marked `imported`, runs from the import day to
  the given date, has no invoice, owes nothing and is not counted as a sale.
  Its true start date is not known.
- **Allowances for real files:** .xlsx and .csv (comma, semicolon or tab;
  UTF-8 or older Windows encoding); loose column headings; phone numbers that
  lost their leading 0; dates as Excel dates, day/month/year, or year-month-day;
  gender as F/M, Female/Male or Gore/Gabo.
- **Limits:** 5 MB, 2,000 rows. Older .xls files are refused with advice to
  save as .xlsx.
- **Safe reading:** openpyxl with defusedxml, so a crafted file cannot abuse
  the XML reader.
- **One audit entry for the import** with the counts, plus the usual entry per
  member registered.

## Export

- Members (with current membership and, with Payments, the balance), payments
  and visits (for a chosen period, up to a year).
- **Owner and branch manager only**, sent with POST, and **each export is
  recorded in the audit log**, because it takes personal data out of the system.
- **Text cells are always written as text**, so a value such as
  `=HYPERLINK(...)` typed as a name cannot run as a formula in Excel.
- Phone numbers are exported in standard form (+250…), which imports back.

## Not done yet

- Importing opening balances (money members owed before).
- Importing past payments or visit history.
- Updating existing members from a spreadsheet (they are skipped).
- Exporting from a specific branch only.
