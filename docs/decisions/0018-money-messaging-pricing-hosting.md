# 0018: Where money goes, messaging, pricing and hosting

Date: 2026-11-17
Status: Accepted (pricing tiers' contents still to be decided)

## Decisions

- **Member payments go straight to each gym's own account.** The platform never
  holds or passes on a gym's money. When mobile money collection is built, each
  gym connects its own MTN MoMo / Airtel Money merchant account, and the app
  only asks for payments and records the confirmations. This keeps the platform
  out of regulated payment-service territory and makes each gym's books its own.
- **Each gym runs its own SMS and WhatsApp.** A gym connects its own provider
  account (sender name, credit, WhatsApp number and templates). The platform
  never sends from a shared account or pays for a gym's messages.
- **Teaching is part of the product.** Alongside messaging, the app will include
  short in-app guides and onboarding on targeted messages (who to contact, when,
  what to say) and on using the member records as a CRM. Paid training sessions
  can be offered on top.
- **Corporate portal** is shaped with the company contact the founder knows;
  nothing is built until those answers come back.
- **Platform prices:** three plans at 75,000, 150,000 and 300,000 RWF. What each
  plan includes, and the billing period, are still to be decided.
- **Hosting in stages:** development and demos on the founder's PC, then a
  stable demo on a server the founder has access to through work, and a
  production server only after discussion with the Rwandan authorities
  responsible (data protection / NCSA). Real member data stays off servers that
  are not cleared for it; demos use the sample gyms.
