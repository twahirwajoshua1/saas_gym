# 0010: Check-in

Date: 2026-11-16
Status: Accepted

## Decisions (`apps/attendance`, the Attendance and Access application)

- **One question decides entry:** `assess(member)` answers whether the member
  may come in today and, if not, why (no membership, expired, not started).
  The screen shows that answer and `check_in` enforces the same rule.
- **Only a membership valid today admits a member.** The last day counts.
- **One check-in per member per day**, enforced by the database. A second
  attempt returns the first record, so a double tap records nothing extra.
- **Owing money warns but does not block**, in line with decision 0009.
- **A renewal warning** shows when 7 or fewer days remain.
- **Who may check in:** owner, branch manager, receptionist and trainer. Not
  sales representatives. Trainers get the check-in screen but no links into
  member records.
- **Phone numbers are partly hidden on the check-in screen** (0788 ••• 456),
  because people standing at the desk can see it.
- **The branch of a check-in** is the staff member's own branch if they are
  tied to one, otherwise the branch the desk chose (remembered for the
  session), otherwise the gym's only branch.
- **Check-ins are not written to the audit log.** There are many per day and
  each record already says who admitted the member and when.
- **Attendance reads the member's balance from billing** when the gym has
  Payments and Finance. It works without it. Because of this, `attendance`
  sits above `billing` in the import rules, although a gym can buy Attendance
  without Payments.
- **The member page shows visits** the same way it shows payments: a separate
  HTMX request to the attendance module.

## Not done yet

- QR codes. They need the member's card, and are built with it.
- Undoing a mistaken check-in.
- Working when the internet connection drops.
- Peak-hour and attendance reports (with the dashboard).
- Door or turnstile hardware.
