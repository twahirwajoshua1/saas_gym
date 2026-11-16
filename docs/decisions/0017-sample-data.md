# 0017: Sample data

Date: 2026-11-17
Status: Accepted

## Decisions

- **`python manage.py create_sample_gyms`** creates five gyms with 1, 2, 3, 4
  and 5 branches, their staff and logins, packages, members, and 75 days of
  history (`--days`, `--scale` and `--seed` change this).
- **Invented names.** Gym names use Kinyarwanda words (Imbaraga, Ubuzima,
  Intare, Umucyo, Inkingi) and are made up; any resemblance to a real business
  is unintended. Fake members and revenue must not appear under a real gym's
  name, for example when showing the product to prospects.
- **Rwandan detail:** Kinyarwanda surnames with common first names (names
  beginning Nyira- or Muka- only for women), MTN (078, 079) and Airtel (072,
  073) numbers, Kigali neighbourhoods plus Musanze, Huye and Rubavu, RWF prices
  that rise with the gym's tier, payments mostly by MTN MoMo with transaction
  references, visits peaking early morning and after work, fewer on Sundays.
- **Made through the real services, with the clock moved back** (time-machine)
  to each simulated day. So every invoice, ledger line, audit entry and check-in
  follows the same rules as real use, and the figures add up. About 35% of
  members are brought in as existing members on the first day, as a gym moving
  from paper would do.
- **Behaviour:** member types (regular, drop-in with day passes, long-term,
  student), renewal rates per type, some renewing early or late, some lapsing
  (and visiting less before they lapse), part payments settled later or left
  owing, an occasional wrong payment reversed and re-recorded, and occasional
  freezes.
- **Safety:** refuses to run when DEBUG is off (unless forced) and refuses to
  run twice, because money records cannot be deleted. Use an empty database.
- Every sample login uses one password, printed at the end. Sample staff are
  not asked to change it.
