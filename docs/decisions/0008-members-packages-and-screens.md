# 0008: Members, packages and the first real screens

Date: 2026-11-16
Status: Accepted

## Members and packages (`apps/members`, the Gym Operations application)

- **Each gym keeps its own details about a member.** `Member` is gym-owned and
  holds the name as that gym wrote it, home branch, gender, date of birth,
  emergency contact and notes. The shared `Person` is only the platform
  identity (the phone number). When a phone number is already known to the
  platform, its Person is reused and left untouched, and the gym is told
  nothing about it. This changes the earlier idea that names are stored only
  once: sharing names across gyms would let one gym see or alter another's data.
- **A member's phone number cannot be changed yet.** It identifies the person
  platform-wide; changing it safely is a separate feature.
- **Packages are archived, never deleted.** Price is whole RWF. Duration is a
  count and a unit (day, week, month, year). Changing a package affects future
  sales only; each sale will keep its own copy of price and duration.
- **Permission checks are inside the services.** Every changing function takes
  `by`, the staff assignment of the person acting. It gives the actor, the gym
  and the role in one value. Screens also check roles, but a future API cannot
  skip the service check. Older services (gyms, modules) still take `actor`
  and will move to this pattern when their screens are built.
- **Roles:** owner manages packages; owner, branch manager, receptionist and
  sales representative register, edit and view members and view packages.
  Trainers have no access to members yet (they will see their own clients with
  the Training application).
- **Services say what is wrong, screens choose the words.** Services raise
  `InvalidDetails(field, reason)`; views turn that into a message.
- **The audit log records that personal details changed, not their contents.**
- **Members and packages are not in the platform admin.** Platform staff do not
  browse gyms' member data; a logged support-access feature comes in Step 7.

## Screens

- **Plain CSS in one file (`static/css/app.css`), not Tailwind.** Tailwind
  needs a Node build step on every developer machine. One stylesheet gives the
  same design with nothing to install and small pages. Revisit if the design
  work outgrows it.
- **System fonts**, so no font files are downloaded on slow connections.
- **HTMX 2.0.11 is copied into `static/vendor/`** (no CDN). First use: the
  member search updates as you type. Pages work without JavaScript too.
- **Layouts:** `templates/layouts/gym.html` (menu plus page, for everything
  inside a gym) and `templates/layouts/plain.html` (centred card, for sign-in).
  `templates/components/field.html` draws one form input with label and errors.
- **Text is marked for translation** (`{% translate %}` and `gettext`) in
  templates, forms and views, so Kinyarwanda and French can be added later
  without rewriting screens. No translations exist yet.
- **Phone numbers are shown the local way** (0788 123 456) and money with
  separators (RWF 30,000). Storage is unchanged.

## Not done yet

- Selling a membership, payments, receipts, check-in, expiry (next increments).
- Excel import and export of members.
- A staff member limited to one branch still sees all the gym's members.
- Suspending or removing a member; waivers.
- Serving static files in production (needed at deployment).
- Figures on the Overview page (comes with the basic dashboard).
