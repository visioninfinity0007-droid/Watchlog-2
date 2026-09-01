#!/usr/bin/env bash
#
# WatchLog marketing site content.
#
# Idempotent: creates pages that do not exist, updates ones that do, and
# can be re-run after any deploy without duplicating anything. The site
# structure therefore lives in git rather than in whatever somebody last
# clicked in wp-admin.
#
# Run inside the WordPress container:
#     docker exec <container> bash /usr/src/wordpress/site-content.sh
#
set -eu   # no pipefail: the container shell may be dash
cd /var/www/html
W="wp --allow-root"

say() { echo "  $*" >&2; }   # stderr: stdout carries ids

# --- clear WordPress's stock content --------------------------------
# "Hello world!" and "Sample Page" on a product site read as abandoned.
IDS=$($W post list --post_type=post --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true
IDS=$($W post list --post_type=page --title="Sample Page" --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true
IDS=$($W post list --post_type=page --title="Privacy Policy" --post_status=draft --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true

# --- helper ----------------------------------------------------------
page() {   # slug  title  <<<content
  local slug="$1" title="$2" body
  body="$(cat)"
  local id
  id=$($W post list --post_type=page --name="$slug" --format=ids 2>/dev/null || true)
  if [ -z "$id" ]; then
    id=$($W post create --post_type=page --post_status=publish \
          --post_name="$slug" --post_title="$title" \
          --post_content="$body" --porcelain)
    say "created /$slug ($id)"
  else
    $W post update "$id" --post_title="$title" --post_content="$body" >/dev/null
    say "updated /$slug ($id)"
  fi
  echo "$id"
}

# --- pages -----------------------------------------------------------

HOME_ID=$($W post list --post_type=page --name="home" --format=ids 2>/dev/null || true)
if [ -z "$HOME_ID" ]; then
  HOME_ID=$($W post create --post_type=page --post_status=publish \
    --post_name=home --post_title="Home" --post_content="" --porcelain)
fi
say "home page id $HOME_ID (rendered by front-page.php)"

HOW=$(page how-it-works "How it works" <<'HTML'
<p class="lede">The mechanics, plainly, including the parts that are unglamorous.</p>

<h2>1. A small program runs at your site</h2>
<p>It installs on any Windows PC on the same network as your recorder — an
office machine, the reception PC, anything that stays switched on. It uses
almost no resources and has no window; it runs quietly in the background.</p>

<h2>2. It connects outward only</h2>
<p>The program opens a connection <em>out</em> to WatchLog. Nothing connects in.
There is no port forwarding, no VPN, and no change to your firewall. Your
recorder is never exposed to the internet, and neither are its credentials —
those stay in a file on that PC and are never sent to us.</p>

<h2>3. It reads what the recorder already logged</h2>
<p>Recorders already detect motion and log events. WatchLog reads that log,
and for each event it takes a still image from the camera at that moment.</p>

<h2>4. It discards the false alarms, on site</h2>
<p>Most motion is not an incident: rain, headlights sweeping a wall, a cat,
the infrared lamp cutting in at dusk. Each still is checked on your own PC
for a person, a car or a motorcycle. If there is none, the event is dropped
and never leaves the building.</p>
<p>This matters more than it sounds. Without it a single recorder produces
hundreds of events a night and the morning report becomes unreadable.</p>

<h2>5. What is left is sent up, and summarised</h2>
<p>Only the events that passed the filter are uploaded, each with its still.
Every morning you get a summary: how many events, on which cameras, how many
after hours, and anything that has stopped working.</p>

<h2>If the internet drops</h2>
<p>Events are stored on the site PC and sent when the connection returns.
Nothing is lost to a bad line.</p>

<h2>What we can and cannot see</h2>
<p>We receive event records and the stills attached to them. We do not have
live access to your cameras, we cannot pan or zoom them, and we cannot browse
your recorded footage. The connection only goes one way.</p>
HTML
)

FEAT=$(page features "Features" <<'HTML'
<p class="lede">What you get, and what each thing is actually for.</p>

<h2>Daily reporting</h2>
<ul>
<li>A morning summary on WhatsApp, and in the portal</li>
<li>Counts by camera and by type, and how many were after hours</li>
<li>First and last event times, in your site's local time</li>
</ul>

<h2>Incidents</h2>
<ul>
<li>Every event logged with a still image from the moment it happened</li>
<li>Filtered on site, so what you read is worth reading</li>
<li>Searchable history in the portal</li>
</ul>

<h2>Site health</h2>
<ul>
<li>Cameras that have gone silent, reported as faults</li>
<li>Recorder faults and tamper events</li>
<li>An alert if the site program itself stops reporting</li>
</ul>
<p>This is the quiet one that earns its keep. A camera that stopped working
three weeks ago is usually discovered on the day you need its footage.</p>

<h2>Analytics</h2>
<ul>
<li>Busiest cameras and busiest hours</li>
<li>Activity by hour in the site's own timezone</li>
<li>Trends across the last 7, 30 or 90 days</li>
</ul>

<h2>More than one site</h2>
<ul>
<li>One view across every location</li>
<li>Per-site recipients — the branch manager gets their branch</li>
<li>Team access with roles: owner, admin, or read-only</li>
</ul>
HTML
)

PRICE=$(page pricing "Pricing" <<'HTML'
<p class="lede">Priced per site, in rupees. No setup fee, no hardware to buy,
no contract.</p>

<table>
<tr><th>Plan</th><th>Per site / month</th><th>Cameras</th><th>History</th></tr>
<tr><td><strong>Starter</strong></td><td>PKR 6,000</td><td>up to 8</td><td>7 days of stills</td></tr>
<tr><td><strong>Growth</strong></td><td>PKR 12,000</td><td>up to 24</td><td>30 days of stills</td></tr>
<tr><td><strong>Enterprise</strong></td><td>Talk to us</td><td>unlimited</td><td>90 days</td></tr>
</table>

<p>Every plan includes the daily report, incident stills, site health,
analytics, and unlimited team members.</p>

<h2>Fourteen days free</h2>
<p>No card. If WatchLog does not work with your recorder you will know within
ten minutes of installing, not after you have paid.</p>

<h2>What could cost you more later</h2>
<p>Nothing is metered. The price is per site, and the only thing that changes
it is adding a site or moving up a camera tier. We would rather tell you the
number than have you discover it.</p>
HTML
)

WHO=$(page who-its-for "Who it's for" <<'HTML'
<p class="lede">Businesses that bought CCTV, had it installed, and have not
opened the footage since.</p>

<h2>Warehouses and yards</h2>
<p>Large perimeters, few people after hours, and a loading bay that matters.
The after-hours count is usually the number that gets read first.</p>

<h2>Retail chains</h2>
<p>One view across every branch, and a per-branch report to the person who
runs it. Head office sees the pattern; the branch sees its own.</p>

<h2>Schools and campuses</h2>
<p>Quiet at night by design, so anything after hours is worth a look. Gate and
boundary cameras carry most of the value.</p>

<h2>Factories</h2>
<p>Shift changes, vehicle movement, and equipment areas. Camera faults matter
as much as incidents — a blind spot on a production floor is a safety issue.</p>

<h2>Offices</h2>
<p>Small camera counts, and the main value is knowing nothing happened, plus
being told immediately when a camera stops working.</p>

<h2>Who it is not for</h2>
<p>If you need someone watching live and responding within minutes, you need a
monitoring contract and a response team, not WatchLog. If your cameras cannot
see what you care about today, WatchLog will not change that.</p>
HTML
)

SETUP=$(page setup "What you need" <<'HTML'
<p class="lede">Stated here, before you sign up, rather than discovered
during it.</p>

<h2>You will need</h2>
<ul>
<li><strong>A supported recorder.</strong> Hikvision, HiLook, Dahua, Imou,
CP Plus, Uniview, Tiandy, or most ONVIF-conformant units.</li>
<li><strong>A Windows PC at the site,</strong> on the same network as the
recorder, that stays switched on. Any ordinary office machine will do.</li>
<li><strong>The recorder's admin username and password.</strong> These are
typed into the program at the site and never leave it.</li>
<li><strong>An ordinary internet connection.</strong> No fixed IP, no port
forwarding, no firewall changes.</li>
</ul>

<h2>What is not supported</h2>
<p>The cheapest unbranded recorders — typically built on Xiongmai or Hisilicon
boards and sold without a brand name — do not speak a standard protocol
reliably enough for us to support them. If you are not sure what you have,
send us a photo of the label and we will tell you before you spend anything.</p>

<h2>Installing, step by step</h2>
<ol>
<li>Create an account and add your site. You will be given an enrollment code.</li>
<li>Download the WatchLog agent onto the site PC.</li>
<li>Run it. A setup wizard asks for the recorder's address and login, then
searches your network if you do not know the address.</li>
<li>Paste the enrollment code. The site appears in your portal within a
minute.</li>
<li>Add who should receive the daily report, and on which channel.</li>
</ol>

<h2>About ten minutes</h2>
<p>Most of it is finding the recorder's password. If the wizard cannot reach
your recorder it tells you why in plain language rather than failing
silently.</p>
HTML
)

ABOUT=$(page about "About" <<'HTML'
<p class="lede">WatchLog is built by Vision Infinity.</p>

<h2>Why this exists</h2>
<p>Almost every business we worked with had cameras, and almost none of them
looked at the footage. The recording was treated as the security. It is not —
it is a record you consult after something has already gone wrong.</p>
<p>The gap was not more cameras or better cameras. It was that nobody was
reading what the cameras already saw. WatchLog closes that gap without asking
anyone to replace equipment they already paid for.</p>

<h2>How we build</h2>
<p>Local first. Your footage stays on your recorder, credentials stay at your
site, and detection runs on your own machine. We receive event records and
stills — the minimum required to tell you what happened.</p>
<p>We would rather state a limitation than let you discover it. That is why
the unsupported recorder list is on the pricing path and not in the small
print.</p>

<h2>Pakistan first</h2>
<p>Priced in rupees. WhatsApp is a first-class channel, not an afterthought.
Built around the recorders that are actually installed here.</p>
HTML
)

CONTACT=$(page contact "Contact" <<'HTML'
<p class="lede">Sales, support, or a straight answer about whether your
recorder will work.</p>

<h2>Before you ask about compatibility</h2>
<p>Send a photo of the label on the front or back of your recorder. That is
usually enough for us to tell you yes or no the same day.</p>

<h2>Support</h2>
<p>If a site has stopped reporting, say which site and roughly when it stopped.
The portal shows the last time each site was heard from, on the site health
page.</p>

<h2>Reaching us</h2>
<p>WhatsApp is the fastest. Email works for anything that needs a paper
trail.</p>
HTML
)

PRIV=$(page privacy "Privacy" <<'HTML'
<p class="lede">What we hold, why, and for how long.</p>

<h2>What stays at your site</h2>
<ul>
<li>Your recorded video. We never receive it and cannot browse it.</li>
<li>Your recorder's username and password. Held in a file on your site PC.</li>
<li>Frames that did not pass the false-alarm filter. Discarded on your
machine.</li>
</ul>

<h2>What we receive</h2>
<ul>
<li>Event records: time, camera, and event type.</li>
<li>One still image per event that passed the filter.</li>
<li>Health signals: when each site and camera was last heard from.</li>
<li>Your account details and the addresses reports are sent to.</li>
</ul>

<h2>How long stills are kept</h2>
<p>By your plan: 7, 30 or 90 days. After that they are deleted. Event records
without images are kept for as long as your account is open.</p>

<h2>Who can see it</h2>
<p>Only people you have invited to your account. Data is separated per
customer at the database level, and that separation is verified by an
automated test before any release.</p>

<h2>People in the images</h2>
<p>Stills may contain identifiable people. You are the controller of that
material; we process it on your behalf to produce your reports. If you need
images removed, ask and we will delete them.</p>
HTML
)

TERMS=$(page terms "Terms" <<'HTML'
<p class="lede">The commitments on both sides, in plain terms.</p>

<h2>What WatchLog does</h2>
<p>It reads the event log of a recorder you own, filters out false alarms,
and reports what is left. It is a reporting service.</p>

<h2>What it does not do</h2>
<p>It does not prevent incidents, does not monitor live, does not dispatch a
response, and is not a substitute for guarding, alarms or insurance. No
reporting service can stop something happening.</p>

<h2>Availability</h2>
<p>If a site's internet drops, events are stored locally and sent when it
returns. If your recorder is off, powered down or unreachable, there is
nothing to report and we will tell you that the site has gone quiet.</p>

<h2>Your responsibilities</h2>
<ul>
<li>Keeping the site PC switched on and connected.</li>
<li>Having the right to place cameras where they are, and to process the
images they capture.</li>
<li>Keeping your account credentials to yourself.</li>
</ul>

<h2>Billing</h2>
<p>Monthly per site, in advance. Cancel any time and it stops at the end of
the paid month. The trial is fourteen days and needs no card.</p>

<h2>Your data if you leave</h2>
<p>Export your event history before you close the account. After closure it
is deleted within thirty days.</p>
HTML
)

# --- settings ---------------------------------------------------------
$W option update show_on_front page >/dev/null
$W option update page_on_front "$HOME_ID" >/dev/null
# The marketing CTAs read this. Configurable via WATCHLOG_PORTAL_URL; the
# sslip default is the DEMO environment only (production sets the env).
$W option update watchlog_portal_url "${WATCHLOG_PORTAL_URL:-https://watchlog.161.97.175.15.sslip.io}" >/dev/null
$W option update blogname "WatchLog" >/dev/null
$W option update blogdescription "Your cameras already see everything. WatchLog tells you what they saw." >/dev/null
$W option update timezone_string "Asia/Karachi" >/dev/null
$W rewrite structure '/%postname%/' --hard >/dev/null 2>&1 || true
$W rewrite flush --hard >/dev/null 2>&1 || true
say "settings applied"

# --- menus ------------------------------------------------------------
build_menu() {  # name  location  ids...
  local name="$1" loc="$2"; shift 2
  $W menu delete "$name" >/dev/null 2>&1 || true
  $W menu create "$name" >/dev/null
  for id in "$@"; do $W menu item add-post "$name" "$id" >/dev/null; done
  $W menu location assign "$name" "$loc" >/dev/null
  say "menu '$name' -> $loc"
}
build_menu primary primary "$HOW" "$FEAT" "$PRICE" "$SETUP"
build_menu footer  footer  "$HOW" "$FEAT" "$PRICE" "$WHO" "$SETUP" "$ABOUT" "$CONTACT" "$PRIV" "$TERMS"

say "done"
$W post list --post_type=page --fields=post_name,post_title,post_status --format=table
