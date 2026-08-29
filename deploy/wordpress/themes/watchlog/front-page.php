<?php
/**
 * Home.
 *
 * Sections follow 03_Design/SITE_MAP.md. Each has one job and hands to
 * the next.
 *
 * ON IMAGERY. There is no photography for this product and the brand
 * guidelines ban the category's stock clichés outright — hooded figures,
 * glowing padlocks, blue "cyber" holograms. So every visual here is
 * authored: inline SVG, or the product's own interface rebuilt in CSS
 * using the real design tokens.
 *
 * That is not a compromise. The most persuasive asset this product has is
 * the thing the customer actually receives, so the hero shows the real
 * WhatsApp report in a phone frame rather than a photograph of a building.
 * Guidelines call this evidence over illustration.
 *
 * Anything that could be mistaken for a customer's real footage is
 * labelled as an example — see the caption under the dashboard.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$portal = get_option('watchlog_portal_url', '#');
?>

<!-- 1 ── Hero ────────────────────────────────────────────────────── -->
<section class="hero">
  <div class="hero-glow" aria-hidden="true"></div>
  <div class="wrap hero-grid">
    <div class="hero-copy">
      <div class="eyebrow"><?php echo watchlog_icon('recorder', 15); ?> For businesses that already have CCTV</div>
      <h1>Your cameras already see everything.<br><span class="hl">WatchLog tells you what they saw.</span></h1>
      <p class="lede">Works with the Hikvision and Dahua recorders you already
        own. No new cameras, no rewiring, no monthly guard.</p>
      <div class="hero-actions">
        <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a 14-day trial</a>
        <a class="btn btn-ghost" href="/how-it-works/">How it works</a>
      </div>
      <ul class="ticks">
        <li><?php echo watchlog_icon('check', 18); ?> No card for the trial</li>
        <li><?php echo watchlog_icon('check', 18); ?> Nothing exposed to the internet</li>
        <li><?php echo watchlog_icon('check', 18); ?> Ten minutes to install</li>
      </ul>
    </div>

    <!-- The actual deliverable. Shown, not described. -->
    <div class="phone" role="img"
         aria-label="Example WhatsApp summary: 14 events, two after midnight at the loading bay, one camera silent for 26 hours.">
      <div class="phone-bar"><?php echo watchlog_icon('whatsapp', 16); ?> WhatsApp</div>
      <div class="phone-screen">
        <div class="bubble">
          <div class="b-head">WatchLog — Karachi Head Office</div>
          <div class="b-date">Friday 28 August</div>
          <p class="b-total"><span class="tabular">14</span> events.</p>
          <p class="b-row"><span class="pill pill-warn">2 after midnight</span> Loading bay</p>
          <p class="b-row"><span class="pill pill-bad">1 fault</span> Camera 3 silent 26h</p>
          <p class="b-row"><span class="pill pill-ok">6 cameras healthy</span></p>
          <p class="b-foot">Busiest: Main Gate (9).<br>First 18:42, last 03:11.</p>
          <span class="b-time">07:00</span>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- 2 ── Compatibility bar ───────────────────────────────────────── -->
<section class="strip">
  <div class="wrap strip-in">
    <span class="strip-label">Works with the recorder you already own</span>
    <ul class="chips small">
      <li>Hikvision</li><li>HiLook</li><li>Dahua</li><li>Imou</li>
      <li>CP Plus</li><li>Uniview</li><li>Tiandy</li><li>ONVIF</li>
    </ul>
  </div>
</section>

<!-- 3 ── The problem ─────────────────────────────────────────────── -->
<section class="alt">
  <div class="wrap split">
    <div>
      <div class="eyebrow">The problem</div>
      <h2>Your cameras record. Nobody watches.</h2>
      <p class="lede">Footage only gets opened after something has already gone
        wrong. By then it is evidence, not security.</p>
      <p>The recorder in the cupboard has been working perfectly all year. That
        is not the same as somebody knowing what it saw.</p>
    </div>

    <!-- A night on a real site: things happened, nobody was told. -->
    <figure class="night" aria-label="A night of activity nobody saw: eleven events between 6pm and 6am.">
      <div class="night-head"><?php echo watchlog_icon('moon', 16); ?> Last night, unreviewed</div>
      <?php
      $hours = ['18:00','20:00','22:00','00:00','02:00','04:00','06:00'];
      $bars  = [2, 1, 0, 3, 4, 1, 0];
      ?>
      <div class="night-bars">
        <?php foreach ($bars as $i => $n) : ?>
          <div class="nb">
            <div class="nb-fill<?php echo $n >= 3 ? ' hot' : ''; ?>"
                 style="height:<?php echo max(6, $n * 22); ?>px"></div>
            <span><?php echo esc_html($hours[$i]); ?></span>
          </div>
        <?php endforeach; ?>
      </div>
      <figcaption>11 events. None seen until somebody went looking.</figcaption>
    </figure>
  </div>
</section>

<!-- 4 ── What arrives ────────────────────────────────────────────── -->
<section>
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">What arrives</div>
      <h2>A summary every morning, read in under a minute</h2>
      <p class="lede">Every incident logged with a still from the moment it
        happened. Nothing needs to be watched live.</p>
    </div>

    <div class="grid g3">
      <div class="card">
        <span class="ico-badge"><?php echo watchlog_icon('report'); ?></span>
        <h3>What happened</h3>
        <p>How many events, on which cameras, and how many were after hours.</p>
      </div>
      <div class="card">
        <span class="ico-badge"><?php echo watchlog_icon('camera'); ?></span>
        <h3>What it looked like</h3>
        <p>A still from the moment of each incident, so you can judge it
          without opening the recorder.</p>
      </div>
      <div class="card">
        <span class="ico-badge warn"><?php echo watchlog_icon('alert'); ?></span>
        <h3>What stopped working</h3>
        <p>A camera gone silent is reported as a fault. That is the failure
          nobody notices for weeks.</p>
      </div>
    </div>

    <!-- The portal, rebuilt in CSS from the same tokens the product uses. -->
    <figure class="ui-shot">
      <div class="ui-chrome"><span></span><span></span><span></span>
        <div class="ui-url">watchlog — dashboard</div></div>
      <div class="ui-body">
        <div class="ui-stats">
          <div class="stat"><b class="tabular">14</b><span>events</span></div>
          <div class="stat"><b class="tabular">2</b><span>after hours</span></div>
          <div class="stat ok"><b class="tabular">6</b><span>cameras up</span></div>
          <div class="stat bad"><b class="tabular">1</b><span>fault</span></div>
        </div>
        <div class="ui-rows">
          <div class="ui-row"><span class="pill pill-bad">fault</span>
            <span class="ui-cam">Camera 3</span>
            <span class="ui-txt">Silent for 26 hours</span>
            <span class="ui-time tabular">—</span></div>
          <div class="ui-row"><span class="pill pill-warn">vehicle</span>
            <span class="ui-cam">Loading bay</span>
            <span class="ui-txt">Vehicle after hours</span>
            <span class="ui-time tabular">03:11</span></div>
          <div class="ui-row"><span class="pill pill-violet">person</span>
            <span class="ui-cam">Main gate</span>
            <span class="ui-txt">Person detected</span>
            <span class="ui-time tabular">01:47</span></div>
          <div class="ui-row"><span class="pill pill-violet">person</span>
            <span class="ui-cam">Main gate</span>
            <span class="ui-txt">Person detected</span>
            <span class="ui-time tabular">00:22</span></div>
        </div>
      </div>
      <figcaption>Example data, not a customer's site.</figcaption>
    </figure>
  </div>
</section>

<!-- 5 ── How it works ────────────────────────────────────────────── -->
<section class="alt">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">How it works</div>
      <h2>Three steps, about ten minutes</h2>
      <p class="lede">The connection only ever goes outward. Nothing of yours is
        put on the internet.</p>
    </div>

    <div class="flow">
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('recorder'); ?></span>
        <h3>1. Your recorder</h3>
        <p>Stays exactly where it is, doing what it already does.</p>
      </div>
      <div class="flow-arrow"><?php echo watchlog_icon('arrow-out', 22); ?></div>
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('pc'); ?></span>
        <h3>2. A PC on site</h3>
        <p>A small program reads the event log, takes a still, and drops the
          false alarms before they leave the building.</p>
      </div>
      <div class="flow-arrow"><?php echo watchlog_icon('arrow-out', 22); ?></div>
      <div class="flow-node">
        <span class="ico-badge"><?php echo watchlog_icon('cloud'); ?></span>
        <h3>3. Your report</h3>
        <p>On WhatsApp each morning, and in the portal whenever you want it.</p>
      </div>
    </div>

    <div class="note">
      <?php echo watchlog_icon('lock', 20); ?>
      <p><strong>No port forwarding, no VPN, no firewall changes.</strong>
        Your recorder's password stays in a file on that PC and is never sent
        to us. We cannot view your cameras live or browse your footage.</p>
    </div>
  </div>
</section>

<!-- 6 ── Compatibility, with the exclusion in the open ───────────── -->
<section>
  <div class="wrap split">
    <div>
      <div class="eyebrow">Compatibility</div>
      <h2>Most of what is installed in Pakistan</h2>
      <ul class="chips">
        <li>Hikvision</li><li>HiLook</li><li>Dahua</li><li>Imou</li>
        <li>CP Plus</li><li>Uniview</li><li>Tiandy</li><li>Most ONVIF recorders</li>
      </ul>
    </div>
    <aside class="callout">
      <span class="ico-badge bad"><?php echo watchlog_icon('alert'); ?></span>
      <h3>What is not supported</h3>
      <p>The cheapest unbranded recorders — typically Xiongmai or Hisilicon
        boards sold without a brand name — do not speak a standard protocol
        reliably enough for us to support.</p>
      <p class="tiny">We would rather tell you here than after you have paid.
        Not sure what you have? Send us a photo of the label.</p>
    </aside>
  </div>
</section>

<!-- 7 ── What it is not ──────────────────────────────────────────── -->
<section class="dark">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Being straight with you</div>
      <h2>What WatchLog is not</h2>
    </div>
    <div class="grid g2 nots">
      <div class="not"><?php echo watchlog_icon('shield-off', 22); ?>
        <div><b>Not a guard service.</b>
          <span>Nobody watches your cameras live. It reads what the recorder
            already logged and tells you.</span></div></div>
      <div class="not"><?php echo watchlog_icon('camera', 22); ?>
        <div><b>Not new cameras.</b>
          <span>It uses the ones you have. If they cannot see the gate today,
            WatchLog will not either.</span></div></div>
      <div class="not"><?php echo watchlog_icon('clock', 22); ?>
        <div><b>Not live monitoring.</b>
          <span>Incidents are logged as they happen and summarised each
            morning. It is not an alarm response service.</span></div></div>
      <div class="not"><?php echo watchlog_icon('alert', 22); ?>
        <div><b>Not a way to prevent anything.</b>
          <span>It observes and reports. Prevention is what guards, gates and
            lighting are for.</span></div></div>
    </div>
  </div>
</section>

<!-- 8 ── Pricing preview ─────────────────────────────────────────── -->
<section class="alt">
  <div class="wrap">
    <div class="section-head">
      <div class="eyebrow">Pricing</div>
      <h2>Per site, in rupees. No setup fee.</h2>
      <p class="lede">Nothing is metered. The only thing that changes the price
        is adding a site or moving up a camera tier.</p>
    </div>
    <div class="grid g3">
      <div class="card plan">
        <h3>Starter</h3>
        <div class="price">PKR 6,000<small> / site / month</small></div>
        <ul class="plan-features">
          <li>Up to 8 cameras</li><li>7 days of stills</li>
          <li>Daily WhatsApp report</li><li>Site health and analytics</li>
        </ul>
      </div>
      <div class="card plan featured">
        <span class="tag">Most sites</span>
        <h3>Growth</h3>
        <div class="price">PKR 12,000<small> / site / month</small></div>
        <ul class="plan-features">
          <li>Up to 24 cameras</li><li>30 days of stills</li>
          <li>Everything in Starter</li><li>Unlimited team members</li>
        </ul>
      </div>
      <div class="card plan">
        <h3>Enterprise</h3>
        <div class="price">Talk to us</div>
        <ul class="plan-features">
          <li>Unlimited cameras</li><li>90 days of stills</li>
          <li>Multi-site rollout</li><li>Priority support</li>
        </ul>
      </div>
    </div>
    <p class="center"><a href="/pricing/">Full pricing detail →</a></p>
  </div>
</section>

<!-- 9 ── Close ───────────────────────────────────────────────────── -->
<section class="close">
  <div class="wrap center">
    <h2>Find out what your cameras have been seeing</h2>
    <p class="lede center-lede">Fourteen days, no card. If it does not work with
      your recorder you will know within ten minutes.</p>
    <div class="hero-actions center-actions">
      <a class="btn btn-primary" href="<?php echo esc_url($portal); ?>">Start a trial</a>
      <a class="btn btn-ghost" href="/contact/">Talk to us first</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
