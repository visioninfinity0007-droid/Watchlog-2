# How WatchLog connects to a customer site

The one-line answer to give the client: **we never connect to you — your
PC connects out to us, the same way a browser opens a website.** So we do
not need your IP address, a static IP, port forwarding, a VPN, or any
change to your firewall.

Demonstrated live on 29 Aug 2026 against the production cloud: an agent
enrolled with nothing but a code, the real Dahua driver identified a
recorder, synced 4 cameras, and pushed 3 events plus 2 images upward —
all outbound. See "What was proven" at the end.

---

## There are two connections, and neither needs your network details

### 1. The site PC → WatchLog (outbound only)

The small program on the site opens a connection **out** to WatchLog over
HTTPS on port 443 — exactly what happens when anyone opens a website.
Because the site initiates it:

- We do not need your IP address. You dial us.
- No static IP. It works on an ordinary connection that changes IP.
- No port forwarding, no firewall change, no VPN.
- **There is no way in.** WatchLog cannot reach into the site even if it
  wanted to — nothing is listening, no door exists. This is the single
  biggest security point, and it is the opposite of how most remote CCTV
  viewing is sold (which puts your recorder on the public internet).

### 2. The site PC → the recorder (same local network)

The agent and the recorder sit on the same LAN. The agent finds the
recorder itself — the installer does **not** need to know its IP:

1. **It asks the network.** An ONVIF discovery broadcast — "are there any
   recorders here?" Many answer instantly.
2. **If none answer** (discovery is often switched off), it scans the
   local network's addresses on the ports recorders use — Dahua 37777,
   Hikvision/ONVIF 80 and 8000, RTSP 554, and a few others.
3. **It shows what it found** and the installer confirms, then types the
   recorder's admin password once.
4. **Or**, if they already know the recorder's IP, they can just type it.

Either way, the recorder's password is entered on the site, stored in a
file on that PC, and **never sent to us**.

---

## What links a site to the right customer account — with no details

A **one-time enrollment code**, generated in the portal for that site
(e.g. `WL-PH3Y-BSVF`). The installer pastes it once. That code is the only
thing that ties this PC to this customer's account.

- No account password is ever entered on the site.
- No IP, hostname or network detail is registered anywhere.
- The code is single-use and expires, so it cannot be reused if it leaks.

In return the agent receives its own secret key, which it uses to prove
itself on every later message. If a site PC is lost, that one key is
revoked — nothing else is exposed.

---

## What the customer actually has to provide

Nothing about their network. Only:

| | |
|---|---|
| A **Windows PC** on the same network as the recorder, left switched on | any ordinary office machine |
| The **recorder's admin username and password** | typed in on site, stays on site |
| An **ordinary internet connection** on that PC | no fixed IP, no firewall change |

If the recorder is one of the cheapest unbranded units (Xiongmai /
Hisilicon boards with no brand name), it may not speak a standard
protocol — tell them to send a photo of the label and we confirm before
they commit.

---

## The whole flow, in order

1. In the portal, the customer adds a site → gets an enrollment code.
2. They run the WatchLog agent on a PC at that site.
3. The agent finds the recorder (discovery, then a scan) and tests the
   login.
4. They paste the enrollment code. The site appears in the portal within
   a minute.
5. From then on the agent, outbound only: reads new events, grabs a still
   for each, discards the false alarms on the PC, and uploads the rest.
6. The daily summary goes out on WhatsApp.

No step requires anyone to know or configure an IP address.

---

## What was proven live, and what still needs a real site

**Proven on 29 Aug against the production cloud**, using a Dahua protocol
simulator and the real `dahua-cgi` driver:

- enrolled with only the code `WL-PH3Y-BSVF`, over outbound HTTPS
- the driver identified the recorder (model NVR4208-8P-4KS2), listed its
  4 cameras, and streamed live events
- 4 cameras synced, 3 events (person, intrusion, vehicle) and 2 stills
  uploaded, heartbeat confirmed — all visible in the database afterwards

**Still needed to finalise — the M1 sign-off gate:** the same run against
**one real recorder** at a real site. The drivers are written for
Hikvision, Dahua and ONVIF but have only ever met a simulator. To close
this we need, for one MVP site:

- the recorder make/model and admin login
- a Windows PC on that LAN we can run the agent on
- someone on site for ten minutes, or remote access to that PC

That single test converts "should work" into "does work" and is the last
thing between here and a live customer.
