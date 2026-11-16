"""
Reading an uploaded spreadsheet into rows.

Accepts Excel (.xlsx) and CSV. The first row must hold column headings; they
are matched loosely ("Phone", "Phone number" and "Telephone" all mean phone).
Every value comes back as text or a date, ready to be checked.
"""

import csv
import io
import re
from datetime import date, datetime

from openpyxl import load_workbook

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000

# Our name for each column, and the headings people use for it.
COLUMNS = {
    "full_name": ["full name", "name", "member name", "names", "amazina"],
    "phone": ["phone", "phone number", "telephone", "tel", "mobile", "phone no", "telefone"],
    "branch": ["branch", "home branch", "location"],
    "gender": ["gender", "sex", "igitsina"],
    "date_of_birth": ["date of birth", "birth date", "birthday", "dob"],
    "emergency_contact_name": ["emergency contact name", "emergency contact", "emergency name"],
    "emergency_contact_phone": ["emergency contact phone", "emergency phone", "emergency number"],
    "notes": ["notes", "note", "comments", "comment"],
    "package": ["package", "membership", "plan", "membership type"],
    "valid_until": ["valid until", "expiry date", "expires", "end date", "expiry", "ends"],
}
REQUIRED = ["full_name", "phone"]

# The headings written in the template, in order.
TEMPLATE_HEADINGS = {
    "full_name": "Full name",
    "phone": "Phone",
    "branch": "Branch",
    "gender": "Gender",
    "date_of_birth": "Date of birth",
    "emergency_contact_name": "Emergency contact name",
    "emergency_contact_phone": "Emergency contact phone",
    "notes": "Notes",
    "package": "Package",
    "valid_until": "Valid until",
}


class UnreadableSpreadsheet(Exception):
    """The file cannot be used. `reason` says why; `detail` may add a fact."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


def _simplify(heading) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(heading or "").lower()).strip()


_LOOKUP = {_simplify(alias): key for key, aliases in COLUMNS.items() for alias in aliases}


def _as_text(value) -> str:
    """Turn a cell into text. Whole numbers lose a trailing ".0" (phones typed as numbers)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat()
    return " ".join(str(value).split())


def parse_date(value) -> date | None:
    """
    Read a date from a cell: an Excel date, "15/12/2026" (day first, as written
    in Rwanda), "15-12-2026", "15.12.2026" or "2026-12-15". None if it is not a date.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _as_text(value)
    patterns = [
        (r"(\d{4})-(\d{1,2})-(\d{1,2})", ("y", "m", "d")),
        (r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", ("d", "m", "y")),
    ]
    for pattern, order in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            parts = dict(zip(order, (int(p) for p in match.groups()), strict=True))
            try:
                return date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                return None
    return None


def _rows_from_xlsx(content: bytes):
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:  # noqa: BLE001 - any failure means "not a readable Excel file"
        raise UnreadableSpreadsheet("not_excel") from error
    try:
        sheet = workbook.worksheets[0]
        for row in sheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def _rows_from_csv(content: bytes):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnreadableSpreadsheet("not_csv")
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        pass
    yield from csv.reader(io.StringIO(text), dialect)


def read_rows(file_name: str, content: bytes) -> list[dict]:
    """
    The spreadsheet's rows as dictionaries keyed by our column names, each with
    "_line" (its row number in the file, for messages). Empty rows are skipped.
    """
    if len(content) > MAX_FILE_BYTES:
        raise UnreadableSpreadsheet("too_big")
    name = (file_name or "").lower()
    if name.endswith(".xlsx"):
        rows = _rows_from_xlsx(content)
    elif name.endswith(".csv"):
        rows = _rows_from_csv(content)
    else:
        raise UnreadableSpreadsheet("wrong_type")

    rows = iter(rows)
    headings = next(rows, None)
    if not headings:
        raise UnreadableSpreadsheet("empty")
    positions = {}
    for index, heading in enumerate(headings):
        key = _LOOKUP.get(_simplify(heading))
        if key and key not in positions:
            positions[key] = index
    missing = [TEMPLATE_HEADINGS[key] for key in REQUIRED if key not in positions]
    if missing:
        raise UnreadableSpreadsheet("missing_columns", ", ".join(missing))

    result = []
    for line, row in enumerate(rows, start=2):
        values = {}
        for key, index in positions.items():
            cell = row[index] if index < len(row) else None
            dated = key in ("date_of_birth", "valid_until") and isinstance(cell, (date, datetime))
            values[key] = cell if dated else _as_text(cell)
        if not any(values.values()):
            continue
        if len(result) >= MAX_ROWS:
            raise UnreadableSpreadsheet("too_many_rows", str(MAX_ROWS))
        values["_line"] = line
        result.append(values)
    if not result:
        raise UnreadableSpreadsheet("no_rows")
    return result
