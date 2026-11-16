# 0015: Member card, QR check-in and the first API endpoint

Date: 2026-11-17
Status: Accepted

## Member card (`apps/memberapp`)

- **A private link per member** (`/card/<token>/`), opened without signing in.
  The token is 43 random characters, so it cannot be guessed. Staff create it,
  can send it by WhatsApp (a wa.me link opens WhatsApp on the staff phone with
  the message ready; nothing is sent automatically), and can make a new one,
  after which the old link stops working. Creating and renewing are audited.
- **The card shows** the gym, the member's name, a partly hidden phone number,
  a QR code, whether the membership is valid today (or why not: expired,
  frozen, not started, none), days left, anything still owed, and the last
  five receipts.
- **Privacy:** anyone holding the link sees that card. Pages are sent with
  no-store, no-referrer and noindex headers. Members are told not to share it.
- **Tenant isolation is kept.** An anonymous request can see no gym's data.
  A small database function (`app_gym_for_card`, SECURITY DEFINER) returns
  only the gym of the member whose token matches exactly. The request then
  works within that gym, under the same row-level security as staff requests.

## QR check-in

- The QR code holds the card's address. At the desk it can be read three ways:
  typed or pasted, a USB or Bluetooth QR scanner (which types into the search
  box), or the device camera where the browser supports barcode detection
  (for example Chrome on Android). The camera path could not be tested here
  (no camera); scanners and typing are tested.
- Scanning only finds the member; staff still press "Check in" (and can see
  the person matches). Another gym's card finds nobody.
- The QR image was decoded from a screenshot during development and matched
  the card address exactly.

## First API endpoint (proof of the migration path)

- `GET /api/v1/card/<token>/` returns the same card as JSON. The page and the
  API call the same service (`card_summary`), which is the architecture's
  promise that a React or React Native member app can be added without
  changing business logic.
- It is a plain Django JSON view. Django REST Framework is still deferred
  until a real app needs more endpoints and token sign-in.
