# WatchLog — Pricing (draft v1)

**Placeholder, for building the pricing page and the portal's plan
records. Not validated against a single real buyer.** Change it freely —
the point is to unblock the build, not to settle the commercials.

Currency PKR, per month, ex-tax.

---

## The tiers

| | **Starter** | **Standard** | **Enterprise** |
|---|---|---|---|
| **Price** | **PKR 2,500** /site/month | **PKR 2,000** /site/month | talk to us |
| Sites | 1 | 2 – 10 | unlimited |
| Cameras per site | up to 8 | up to 24 | unlimited |
| Daily report | yes | yes | yes |
| Incident stills | 7 days | 30 days | 90 days, negotiable |
| Site health alerts | yes | yes | yes |
| Users | 2 | 10 | unlimited |
| Multi-site view | — | yes | yes |
| WhatsApp delivery | yes | yes | yes |
| Support | email | email + WhatsApp | named contact |
| Setup help | self-serve | remote assisted | on-site |

**Free trial: 14 days, no card.** The trial has to be long enough to
include a weekend — most sites are quietest then, and the contrast is
what makes the daily report land.

---

## Why it is shaped this way

**Per site, not per camera.** Cameras are how the customer counts, but
sites are what actually cost us: one agent, one connection, one report.
Camera counts are a band inside the tier so a 30-camera warehouse does
not pay 30×, which would price us above the value.

**The volume break is real, not decorative.** A second site costs us
almost nothing beyond the first, so PKR 2,000 at scale is honest rather
than a discount trick.

**Retention is the upgrade lever.** Image storage is the one genuinely
variable cost, and 7 → 30 → 90 days is a difference customers understand
without explanation.

**No per-event or per-alert pricing, ever.** A customer who is charged
per incident is a customer who turns cameras off. That would break the
product's whole premise.

---

## What must be true before this goes on a public page

1. **Retention must be enforced in code before it is sold.**
   `wl_prune_snapshots()` currently defaults to 14 days / 5,000 images
   for everyone. Selling 7/30/90 means making retention per-tier and
   proving it runs. Publishing a number the system does not enforce is
   the kind of thing that ends a contract.

2. **Billing does not exist.** Safepay or Switch, plus KYC, plus the
   lead time that is outside our control. Until then the honest page says
   "contact us" rather than showing a checkout that isn't there.

3. **The camera bands need a sanity check against a real site.** 8 and 24
   are guesses. A single AKSS site would settle it.

4. **These numbers have not been tested on anyone.** They are anchored on
   the PKR 5,000/month platform fee already in the AKSS engagement — one
   data point, from a client who is also the pilot. Before this is
   printed, put it in front of three prospects who are not AKSS.

---

## Open questions for the owner

- Is the trial self-serve, or does every trial get a human install visit?
  That answers whether the funnel is product-led or sales-led, and it
  changes the whole marketing site.
- Annual pricing with a discount, or monthly only?
- Does the reseller case (a security company managing client sites) get
  its own tier? That is AKSS's own use case and the most likely channel.
