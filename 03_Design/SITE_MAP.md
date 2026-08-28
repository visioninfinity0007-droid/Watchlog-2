# WatchLog — Site Map and Page Structure

Structure and intent for the public marketing site. Copy is drafted here
so it can be reviewed before anything is built, exactly as Cargo Max did.

**One rule above all others:** the requirements are stated *before*
sign-up, not discovered during it. A customer who signs up and then
learns they need a PC at the site is a refund.

---

## Structure

```
/                     Home
/how-it-works         The honest mechanics
/features             What you get
/pricing              Plans
/who-its-for          Segments
/setup                Requirements + install guide      ← unusual, deliberate
/about                Vision Infinity, credibility
/contact              Sales and support
/privacy  /terms      Required for a paid SaaS
--------------------------------------------------
app.watchlog.<domain> Portal (separate app, same skin)
```

`/setup` existing as a public page is a deliberate choice. Most SaaS
hides the requirements. Ours involves installing software on a PC at the
customer's premises — putting that in the open filters out people it will
not work for, and reassures the ones it will.

---

## Home

Sections in order. Each has one purpose and hands to the next.

**1. Hero**
> ## Your cameras already see everything.
> ## WatchLog tells you what they saw.
>
> Works with the Hikvision and Dahua recorders you already own. No new
> cameras, no rewiring, no monthly guard.
>
> `[See a sample report]`  `[How it works]`

No stock imagery. The visual is a real dashboard screenshot on the dark
canvas — evidence, not illustration.

**2. The problem, in one line each**
> Your cameras record. Nobody watches.
> Footage only gets opened after something has already gone wrong.

**3. What arrives** — the actual product, shown not described.
A real daily report: 14 events, two after midnight at the loading bay,
one camera silent for a day. With the still images.

**4. How it works — three steps**
1. Install a small program on a PC at your site
2. It finds your recorder and connects to it
3. Reports arrive every morning

**5. Works with what you have**
Hikvision, HiLook, Dahua, Imou, CP Plus, Uniview, Tiandy, and most ONVIF
recorders. **State the exclusion here, not in the small print:** the
cheapest unbranded recorders are not supported.

**6. What it is not** — an unusually honest section, and a differentiator.
> Not a guard service. Not new cameras. Not live monitoring.
> It watches the recorder you already have and tells you what happened.

**7. Close** — dark band, single call to action.

---

## /how-it-works

The mechanics, plainly, including the parts that are unglamorous.

- The agent runs on a Windows PC on the same network as the recorder
- It only ever makes outbound connections — no port forwarding, no VPN,
  nothing opened to the internet
- **Your recorder's password never leaves the site**
- Only event details and small still images travel upward
- If the internet drops, events queue on disk and go up when it returns

That fourth point is the strongest trust argument in the whole product
and belongs on its own, not buried in a features grid.

---

## /features

Grouped, with a real screenshot each.

| Group | Contents |
|---|---|
| Daily reporting | Morning summary, after-hours counts, per-camera breakdown |
| Incidents | Every event logged with a still image at the moment it happened |
| Site health | Cameras gone silent, recorder faults, agent offline |
| Analytics | Busiest cameras, activity by hour in site local time, trends |
| Multi-site | One view across every location |

**No accuracy or reliability percentage appears anywhere** until it has
been measured on real footage.

---

## /pricing

**Blocked — tiers do not exist yet.** Structure only:

- Priced per site, with a camera band
- A free trial with a stated length
- What every plan includes
- What costs extra
- A plain FAQ: what happens at trial end, can you cancel, where is data
  held, how long are images kept

Note for whoever fills this in: image retention is currently 14 days by
count and age. That is a *product* commitment once published — do not
publish a number the system does not enforce.

---

## /setup — requirements, in public

> **What you need**
> - A Windows PC at the site, left switched on, on the same network as
>   the recorder
> - The recorder's admin username and password
> - The recorder's web interface enabled
>
> **What you do not need**
> - New cameras
> - A static IP or port forwarding
> - To give us your recorder's password

Then the install walkthrough with real screenshots of the setup wizard,
and a troubleshooting section covering the failures already seen in the
field: recorder on a non-standard port, PC on a different network, ONVIF
discovery disabled.

---

## /who-its-for

Warehousing and logistics · Retail chains · Schools · Factories ·
Offices and business parks · Security companies managing client sites.

That last one matters: it is AKSS's own use case and the likeliest
reseller channel.

---

## Global

**Header** — WatchLog mark, How it works, Features, Pricing, Setup,
`[Log in]` (to the portal), `[Start free trial]`.

**Footer** — dark band. Product, Company, Legal, Support. Supported
recorder brands repeated as trust evidence.

`[Log in]` must land in the portal with no visible seam: same header,
same type, same colours, straight from the shared tokens.

---

## Assets required

Listed so they can be produced or commissioned in one pass rather than
scavenged mid-build.

| # | Asset | Notes |
|---|---|---|
| 1 | Logo — full, mark, favicon | Per BRAND_GUIDELINES §7. Not yet drawn. |
| 2 | Dashboard screenshot, dark | Real product. Blur any real footage. |
| 3 | Daily report screenshot | The thing customers actually receive |
| 4 | Incident card with still | Faces and plates blurred, always |
| 5 | Setup wizard screenshots ×3 | Find recorder / login / camera list |
| 6 | Diagram: site → agent → cloud → report | Simple, token colours |
| 7 | Recorder brand logos | Check each brand's trademark usage terms |
| 8 | Premises photography ×4 | Ordinary businesses, daylight, no drama |
| 9 | OG / social card | 1200×630 |

**Banned throughout:** hooded figures, padlocks, shields, glowing globes,
red CCTV-noir. Per BRAND_GUIDELINES §6.
