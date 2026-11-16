# 0014: Freezing and extending memberships

Date: 2026-11-17
Status: Accepted

## Decisions

- **Owner and branch manager** can extend or freeze a membership that is
  current or coming, always with a reason. Both are recorded in the audit log.
- **Extend** adds 1 to 365 days to the end, for example as compensation.
- **Freeze** pauses a membership for 1 to 90 days from today or later. The
  member is refused at check-in during the freeze (with the date it ends), and
  the membership's end moves later by the same number of days. Freezes on one
  membership cannot overlap.
- **A freeze can be ended early.** If it has begun on an earlier day it ends
  yesterday and the unused days are taken off the end again. If it starts today
  or later it is called off entirely (kept as history, marked cancelled), so the
  member can come in at once.
- **Neither may run into the member's next membership.** The owner changes that
  one instead.
- **A frozen member still counts as an active member** on the dashboard: they
  hold a paid, valid membership.
- Freezes are free; nothing is charged. Freeze fees, if a gym wants them, would
  be sold as a separate item later.
- Freezes are gym-owned and protected by tenant isolation (lock 2).
