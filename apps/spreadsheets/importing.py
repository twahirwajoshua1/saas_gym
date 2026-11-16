"""
Importing members from a spreadsheet, in two steps.

  1. check_rows: every row is checked and labelled ready, already a member, or
     with its problems. Nothing is saved.
  2. import_rows: the ready rows are saved, all together or not at all.

The rules for a valid member are the members module's own (check_registration,
register_member), so an imported member is held to the same standard as one
typed in at the desk.
"""

from dataclasses import dataclass, field

from django.db import transaction

from apps.audit import services as audit
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.gyms.permissions import MANAGER_ROLES, require_role
from apps.members import services as members
from apps.members.models import Gender
from apps.people.phones import InvalidPhoneNumber, normalize_phone
from apps.spreadsheets.reading import parse_date

READY = "ready"
EXISTING = "existing"
PROBLEM = "problem"

# Ways people write gender, including Kinyarwanda (gore, gabo).
GENDER_WORDS = {
    "f": Gender.FEMALE,
    "female": Gender.FEMALE,
    "woman": Gender.FEMALE,
    "gore": Gender.FEMALE,
    "m": Gender.MALE,
    "male": Gender.MALE,
    "man": Gender.MALE,
    "gabo": Gender.MALE,
}


@dataclass
class CheckedRow:
    line: int
    values: dict
    status: str = READY
    problems: list[str] = field(default_factory=list)  # "field:reason" codes
    existing_name: str = ""

    @property
    def name(self) -> str:
        return self.values.get("full_name", "")


@dataclass
class ImportCheck:
    rows: list[CheckedRow]

    def _with(self, status):
        return [row for row in self.rows if row.status == status]

    @property
    def ready(self):
        return self._with(READY)

    @property
    def existing(self):
        return self._with(EXISTING)

    @property
    def problems(self):
        return self._with(PROBLEM)


@dataclass
class ImportResult:
    members_added: int
    memberships_added: int
    skipped: int


def _branches_by_name(gym: Gym) -> dict:
    branches = Branch.objects.for_gym(gym).filter(is_active=True)
    return {" ".join(b.name.lower().split()): b for b in branches}


def _details(row: dict, branch) -> members.MemberDetails:
    gender = GENDER_WORDS.get(str(row.get("gender", "")).strip().lower(), "")
    dob = row.get("date_of_birth")
    return members.MemberDetails(
        full_name=row.get("full_name", ""),
        branch=branch,
        gender=gender,
        date_of_birth=parse_date(dob) if dob else None,
        emergency_contact_name=row.get("emergency_contact_name", ""),
        emergency_contact_phone=row.get("emergency_contact_phone", ""),
        notes=row.get("notes", ""),
    )


def _check_one(gym: Gym, row: dict, branches: dict, seen_phones: dict) -> CheckedRow:
    checked = CheckedRow(line=row["_line"], values=row)
    problems = checked.problems

    branch = None
    branch_name = " ".join(str(row.get("branch", "")).lower().split())
    if branch_name:
        branch = branches.get(branch_name)
        if branch is None:
            problems.append("branch:not_found")
    elif len(branches) > 1:
        problems.append("branch:required")

    if row.get("gender") and str(row["gender"]).strip().lower() not in GENDER_WORDS:
        problems.append("gender:invalid")
    if row.get("date_of_birth") and parse_date(row["date_of_birth"]) is None:
        problems.append("date_of_birth:invalid")

    try:
        phone = normalize_phone(row.get("phone", ""))
    except InvalidPhoneNumber:
        phone = None
    if phone and phone in seen_phones:
        problems.append(f"phone:repeated:{seen_phones[phone]}")
    elif phone:
        seen_phones[phone] = checked.line

    try:
        members.check_registration(gym, phone=row.get("phone", ""), details=_details(row, branch))
    except members.AlreadyMember as already:
        if not problems:
            checked.status = EXISTING
            checked.existing_name = already.member.full_name
            return checked
    except members.InvalidDetails as error:
        code = f"{error.field}:{error.reason}"
        if code not in problems and not (error.field == "branch" and branch_name):
            problems.append(code)

    package_name, valid_until = row.get("package", ""), row.get("valid_until")
    if package_name or valid_until:
        if not package_name:
            problems.append("package:required")
        elif not valid_until:
            problems.append("valid_until:required")
        else:
            try:
                members.check_existing_membership(
                    gym, package_name=package_name, valid_until=parse_date(valid_until)
                )
            except members.InvalidDetails as error:
                problems.append(f"{error.field}:{error.reason}")

    if problems:
        checked.status = PROBLEM
    return checked


def check_rows(gym: Gym, rows: list[dict]) -> ImportCheck:
    """Check every row without saving anything."""
    branches, seen = _branches_by_name(gym), {}
    return ImportCheck(rows=[_check_one(gym, row, branches, seen) for row in rows])


@transaction.atomic
def import_rows(*, by: StaffAssignment, rows: list[dict], file_name: str = "") -> ImportResult:
    """
    Save every row that is (still) ready. Owner and branch manager.

    The rows are checked again first, because things may have changed since the
    preview (someone may have registered one of these members at the desk).
    Everything is saved in one transaction: if anything unexpected goes wrong,
    nothing is saved.
    """
    require_role(by, MANAGER_ROLES)
    check = check_rows(by.gym, rows)
    branches = _branches_by_name(by.gym)

    members_added = memberships_added = 0
    for row in check.ready:
        values = row.values
        branch = branches.get(" ".join(str(values.get("branch", "")).lower().split()))
        member = members.register_member(
            by=by, phone=values["phone"], details=_details(values, branch)
        )
        members_added += 1
        if values.get("package"):
            members.record_existing_membership(
                by=by,
                member=member,
                package=members.find_package_by_name(by.gym, values["package"]),
                valid_until=parse_date(values["valid_until"]),
            )
            memberships_added += 1

    skipped = len(check.rows) - members_added
    audit.record(
        action="members.imported",
        summary=(
            f"{members_added} members imported from a spreadsheet"
            f"{f' ({file_name})' if file_name else ''}; {skipped} rows skipped"
        ),
        gym=by.gym,
        actor=by.person,
        details={
            "file": file_name[:120],
            "members_added": members_added,
            "memberships_added": memberships_added,
            "skipped": skipped,
        },
    )
    return ImportResult(members_added, memberships_added, skipped)
