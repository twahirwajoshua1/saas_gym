# 0003: People and login

Date: 2026-11-16
Status: Accepted

## Decisions

- **A Person exists once on the platform**, identified by phone number. Other
  modules link to the Person instead of storing names and numbers again.
- **Phone numbers are stored in one standard form** (`+250788123456`). Numbers
  written without a country code are treated as Rwandan. This is what makes
  duplicate detection reliable.
- **A login account (User) is separate from the Person** and optional. Most
  members are registered at reception without a login and may get one later.
- **Custom user model from the first migration** (`accounts.User`), signing in
  with phone number and password. Password hashing, sessions and permissions
  are Django's own.
- **The login phone is kept on the User as well as the Person**, because
  Django needs the sign-in field on the user model. The two are always equal
  and only the accounts services write the User's copy. When a "change phone
  number" feature is built, it must update both in one transaction.
- **People have a random public identifier** (`public_id`) for use in web
  addresses and the future API, so internal counting numbers are never exposed.
- **`is_staff` means staff of the platform itself**, not staff of a gym. Gym
  roles are a separate concept built in the next increment.

## Not done yet

- One-time codes by SMS (needs an SMS provider).
- Limiting repeated wrong-password attempts.
- Password reset.
- Changing a phone number.
