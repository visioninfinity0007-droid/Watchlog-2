<?php
/**
 * Homepage — WatchLog. 14 sections, dark at the conceptual peaks.
 * Copy is the final deck in docs/design/COPY_DECK.md; claims conform to
 * docs/design/PUBLIC_CLAIMS_MATRIX.md. Product screenshots come from the
 * real demo tenant (watchlog_shot placeholders until captured).
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>

<!-- 01 · HERO -->
<section class="hero dark field">
  <?php echo watchlog_pic('hero-industry-atmosphere', '', 2000, 1125, 'hero-bg', '100vw', true); ?>
  <div class="wrap-wide hero-grid">
    <div class="hero-copy reveal in">
      <span class="eyebrow">CCTV intelligence for businesses</span>
      <h1>Make your existing cameras useful every&nbsp;day.</h1>
      <p class="lead measure">WatchLog turns your recorder's events into validated incidents,
        camera-health visibility and a daily report — without exposing your CCTV to the internet.</p>
      <div class="cta-row">
        <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a>
      </div>
      <ul class="chips">
        <li class="chip"><?php echo watchlog_icon('check', 18); ?> Works with existing CCTV</li>
        <li class="chip"><?php echo watchlog_icon('check', 18); ?> On-site AI filtering</li>
        <li class="chip"><?php echo watchlog_icon('check', 18); ?> 14-day trial, no card</li>
      </ul>
    </div>
    <div class="hero-shot reveal in glow">
      <?php echo watchlog_shot('product-hero-composite', 'The WatchLog portal: overview, an incident with its still, and a daily report', 1600, 1100, '(max-width:900px) 92vw, 52vw', true, true); ?>
    </div>
  </div>
</section>

<!-- 02 · PROOF STRIP -->
<section class="dark navy proof sec-sm">
  <div class="wrap-wide proof-grid">
    <div class="proof-item"><?php echo watchlog_icon('camera', 22); ?><span>Works with the CCTV you already own</span></div>
    <div class="proof-item"><?php echo watchlog_icon('check', 22); ?><span>Filters false alarms on site</span></div>
    <div class="proof-item"><?php echo watchlog_icon('lock', 22); ?><span>No inbound access to your recorder</span></div>
    <div class="proof-item"><?php echo watchlog_icon('report', 22); ?><span>Portal + a daily report</span></div>
  </div>
</section>

<!-- 03 · PROBLEM / CONTEXT -->
<section class="light">
  <div class="wrap split split-5-7">
    <div class="reveal">
      <span class="eyebrow">The gap</span>
      <h2>Your CCTV creates footage. WatchLog creates visibility.</h2>
      <p class="lead">Most cameras are only ever opened after something has already gone wrong.</p>
      <p>WatchLog reads the events your recorder already logs and turns them into an operational
        record you actually use — a short daily read instead of hours of footage.</p>
    </div>
    <div class="reveal">
      <?php echo watchlog_pic('environment-recorder', 'A CCTV recorder on a shelf at a site', 1800, 1200, 'frame-plain', '(max-width:900px) 92vw, 46vw'); ?>
    </div>
  </div>
</section>

<!-- 04 · PLATFORM REVEAL -->
<section class="dark field platform-reveal">
  <div class="wrap-wide">
    <div class="sec-head center reveal">
      <span class="eyebrow">The platform</span>
      <h2>One place to understand every site.</h2>
      <p class="lead measure">Incidents, site health and after-hours activity across all your
        locations — in a single view.</p>
    </div>
    <div class="reveal glow platform-shot">
      <?php echo watchlog_shot('product-platform-overview', 'WatchLog Overview: sites, incidents, after-hours activity and site health', 1900, 1150, '(max-width:1200px) 92vw, 1100px', true); ?>
    </div>
    <div class="platform-chips reveal">
      <span class="pchip"><?php echo watchlog_icon('camera',18); ?> Incidents</span>
      <span class="pchip"><?php echo watchlog_icon('alert',18); ?> Site health</span>
      <span class="pchip"><?php echo watchlog_icon('moon',18); ?> After-hours</span>
      <span class="pchip"><?php echo watchlog_icon('check',18); ?> Camera health</span>
    </div>
    <div class="center" style="margin-top:36px">
      <a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See the platform <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<!-- 05 · THREE CAPABILITIES -->
<section class="light">
  <div class="wrap">
    <div class="sec-head reveal"><span class="eyebrow">What you get</span>
      <h2>Three things, done properly.</h2></div>

    <div class="cap split split-7-5 reveal">
      <div class="cap-media"><?php echo watchlog_shot('product-incidents', 'WatchLog Incidents with filters and a person incident selected', 1500, 1000, '(max-width:900px) 92vw, 55vw', true); ?></div>
      <div class="cap-copy">
        <span class="cap-k"><?php echo watchlog_icon('camera',20); ?> Incidents</span>
        <h3>Know what happened.</h3>
        <p>Every kept event carries a still, filtered on site so what you review is worth reviewing.
          Search the history by site, camera and type.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">Explore incidents <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
    </div>

    <div class="cap split split-5-7 reveal flip">
      <div class="cap-copy">
        <span class="cap-k"><?php echo watchlog_icon('report',20); ?> Reporting</span>
        <h3>Wake up to the useful part.</h3>
        <p>A daily summary to the right people, on WhatsApp or email, in each site's local time —
          with a full history kept in the portal.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
      <div class="cap-media"><?php echo watchlog_shot('product-reports', 'WatchLog Reports: a daily report with delivery history and recipients', 1500, 1000, '(max-width:900px) 92vw, 55vw', true); ?></div>
    </div>

    <div class="cap split split-7-5 reveal">
      <div class="cap-media"><?php echo watchlog_shot('product-site-health', 'WatchLog Site Health showing a healthy site and a camera fault', 1500, 1000, '(max-width:900px) 92vw, 55vw', true); ?></div>
      <div class="cap-copy">
        <span class="cap-k"><?php echo watchlog_icon('alert',20); ?> Site Health</span>
        <h3>Know when something goes quiet.</h3>
        <p>A camera that stopped three weeks ago is usually found the day you need its footage.
          WatchLog tells you as soon as a camera or a site goes silent.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See site health <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
    </div>
  </div>
</section>

<!-- 06 · LOCAL AI -->
<section class="dark field ai">
  <div class="wrap-wide">
    <div class="sec-head center reveal"><span class="eyebrow">On-site intelligence</span>
      <h2>Less noise. More useful incidents.</h2>
      <p class="lead measure">Rain, headlights and the IR lamp at dusk all trip motion. WatchLog checks
        each still on your own site PC and keeps only person, car and motorcycle — before anything is sent.</p>
    </div>
    <div class="reveal ai-diagram">
      <?php echo watchlog_pic('diagram-ai-filtering', 'Raw event to local AI to a validated, kept incident; if the detector cannot run, the event is kept rather than dropped', 1800, 1000, '', '(max-width:1200px) 92vw, 1100px'); ?>
    </div>
    <div class="ai-strip reveal">
      <div class="ai-col">
        <span class="ai-lbl kept">Kept</span>
        <div class="ai-thumbs">
          <?php echo watchlog_pic('cctv-person','Example CCTV still: a person',1600,900,'', '30vw'); ?>
          <?php echo watchlog_pic('cctv-vehicle','Example CCTV still: a vehicle',1600,900,'', '30vw'); ?>
          <?php echo watchlog_pic('cctv-motorcycle','Example CCTV still: a motorcycle',1600,900,'', '30vw'); ?>
        </div>
      </div>
      <div class="ai-col">
        <span class="ai-lbl dropped">Filtered out</span>
        <div class="ai-thumbs">
          <?php echo watchlog_pic('cctv-empty-rain','Example CCTV still: empty scene in rain',1600,900,'', '30vw'); ?>
          <?php echo watchlog_pic('cctv-headlights','Example CCTV still: headlights sweeping a wall',1600,900,'', '30vw'); ?>
        </div>
      </div>
    </div>
    <p class="center note-line reveal">Detects person, car and motorcycle. Not facial recognition.</p>
  </div>
</section>

<!-- 07 · HOW IT WORKS -->
<section class="light">
  <div class="wrap">
    <div class="sec-head reveal"><span class="eyebrow">How it works</span>
      <h2>Your recorder stays private.</h2>
      <p class="lead measure">A small program on a PC you already have reads the recorder and connects
        outward only — no port forwarding, no inbound access.</p></div>
    <div class="reveal hiw-diagram">
      <?php echo watchlog_pic('diagram-architecture', 'Recorder to Windows Site Agent to secure outbound to WatchLog cloud to portal and reports', 1800, 1000, '', '(max-width:1100px) 92vw, 1000px'); ?>
    </div>
    <div class="grid g3 hiw-points reveal">
      <div class="card"><?php echo watchlog_icon('lock',24); ?><h3>Recorder stays private</h3><p>Never exposed to the public internet. No port forwarding, no VPN.</p></div>
      <div class="card"><?php echo watchlog_icon('pc',24); ?><h3>Credentials stay on site</h3><p>The recorder's login lives in a file on your site PC and is never sent to WatchLog.</p></div>
      <div class="card"><?php echo watchlog_icon('arrow-out',24); ?><h3>Connection goes outward</h3><p>Only validated event data and one still per incident leave the building.</p></div>
    </div>
    <div style="margin-top:36px"><a class="arrow-link" href="<?php echo watchlog_url('how-it-works'); ?>">How WatchLog works <?php echo watchlog_icon('arrow-right',18); ?></a></div>
  </div>
</section>

<!-- 08 · DAILY OPERATIONS -->
<section class="cloud">
  <div class="wrap split split-7-5">
    <div class="reveal">
      <?php echo watchlog_shot('product-reports', 'A WatchLog daily report in the portal with delivery history', 1500, 1000, '(max-width:900px) 92vw, 55vw', true); ?>
    </div>
    <div class="reveal">
      <span class="eyebrow">Every morning</span>
      <h2>The useful part, before your first coffee.</h2>
      <p>Each morning the right people get a short summary — counts by camera and type, how many were
        after hours, and anything that went quiet.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('whatsapp',20); ?> WhatsApp, email, or both</li>
        <li><?php echo watchlog_icon('clock',20); ?> Counts in each site's local time</li>
        <li><?php echo watchlog_icon('report',20); ?> Full delivery history in the portal</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<!-- 09 · MULTI-SITE -->
<section class="dark field">
  <div class="wrap split split-5-7">
    <div class="reveal">
      <span class="eyebrow">More than one site</span>
      <h2>One view across every location.</h2>
      <p>Head office sees the pattern; each site's report goes to the person who runs it. Roles for
        owner, admin and read-only, and per-site recipients.</p>
      <ul class="ticks light-ticks">
        <li><?php echo watchlog_icon('sites',20); ?> Every site in one overview</li>
        <li><?php echo watchlog_icon('people',20); ?> Per-site recipients and team roles</li>
        <li><?php echo watchlog_icon('chart',20); ?> Compare activity across locations</li>
      </ul>
    </div>
    <div class="reveal">
      <?php echo watchlog_pic('diagram-multi-site','One WatchLog account across multiple sites',1800,1000,'', '(max-width:900px) 92vw, 55vw'); ?>
    </div>
  </div>
</section>

<!-- 10 · SOLUTIONS -->
<section class="light">
  <div class="wrap-wide">
    <div class="sec-head reveal"><span class="eyebrow">Built for your sites</span>
      <h2>WatchLog for the sites you already operate.</h2></div>
    <div class="sol-grid reveal">
      <?php
      $sols = [
        ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','See what happened after the shift ended.'],
        ['solutions/retail','solution-retail','Retail','Understand every branch without calling every branch.'],
        ['solutions/manufacturing','solution-manufacturing','Manufacturing','Visibility across shifts, gates and critical areas.'],
        ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Know what moved after hours.'],
        ['solutions/offices','solution-office-commercial','Offices & Commercial','Daily visibility without watching screens.'],
      ];
      foreach ($sols as $s) {
        printf('<a class="sol-tile" href="%s">%s<div class="sol-body"><h3>%s</h3><p>%s</p>'
          .'<span class="sol-more">Explore %s</span></div></a>',
          watchlog_url($s[0]),
          watchlog_pic($s[1], $s[2], 1800, 1200, 'sol-img', '(max-width:640px) 92vw, (max-width:1000px) 46vw, 30vw'),
          esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])));
      }
      ?>
    </div>
  </div>
</section>

<!-- 11 · SECURITY -->
<section class="dark navy security">
  <div class="wrap split split-7-5">
    <div class="reveal">
      <?php echo watchlog_pic('diagram-privacy','WatchLog privacy model: outbound-only, recorder private',1800,1000,'', '(max-width:900px) 92vw, 55vw'); ?>
    </div>
    <div class="reveal">
      <span class="eyebrow">Security</span>
      <h2>Security by architecture, not by promise.</h2>
      <ul class="ticks light-ticks">
        <li><?php echo watchlog_icon('shield',20); ?> Your recorder is never exposed to the public internet</li>
        <li><?php echo watchlog_icon('lock',20); ?> Credentials stay on the site PC</li>
        <li><?php echo watchlog_icon('camera',20); ?> Recorded video stays on your recorder</li>
        <li><?php echo watchlog_icon('arrow-out',20); ?> Only validated incident data is synced</li>
        <li><?php echo watchlog_icon('check',20); ?> Tenant isolation enforced in the database, tested before every release</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('security'); ?>">Explore security <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<!-- 12 · COMPATIBILITY -->
<section class="light">
  <div class="wrap split split-7-5">
    <div class="reveal">
      <?php echo watchlog_pic('diagram-compatibility','Keep your cameras and recorder, add WatchLog',1800,1000,'frame-plain', '(max-width:900px) 92vw, 55vw'); ?>
    </div>
    <div class="reveal">
      <span class="eyebrow">Compatibility</span>
      <h2>Keep the cameras. Keep the recorder. Add WatchLog.</h2>
      <p>WatchLog works with the recorder you already own. Hikvision and Dahua are validated; HiLook,
        Imou, CP&nbsp;Plus, Uniview, Tiandy and most ONVIF recorders are protocol-compatible.</p>
      <p>Not sure what you have? Send us your recorder's label and we'll confirm before you spend anything.</p>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a></div>
    </div>
  </div>
</section>

<!-- 13 · PRICING / TRIAL -->
<section class="cloud">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Pricing</span>
      <h2>Priced per site. Start free for 14 days.</h2>
      <p class="lead measure">No setup fee, no hardware to buy. When a trial ends, reporting pauses —
        your events and history are kept.</p></div>
    <div class="grid g3 price-grid reveal">
      <div class="price-card">
        <h3>Starter</h3><p class="price"><span>PKR</span> 6,000<small>/site / month</small></p>
        <p class="price-for">For smaller sites getting daily visibility.</p>
        <a class="btn btn-secondary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card featured">
        <span class="price-tag">Most popular</span>
        <h3>Growth</h3><p class="price"><span>PKR</span> 12,000<small>/site / month</small></p>
        <p class="price-for">More cameras and a longer history of stills.</p>
        <a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card">
        <h3>Enterprise</h3><p class="price price-talk">Talk to us</p>
        <p class="price-for">Multi-site, unlimited cameras, longest retention.</p>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
      </div>
    </div>
    <p class="center" style="margin-top:24px"><a class="arrow-link" href="<?php echo watchlog_url('pricing'); ?>">View full pricing <?php echo watchlog_icon('arrow-right',18); ?></a></p>
  </div>
</section>

<!-- 14 · FINAL CTA -->
<section class="dark field final-cta">
  <?php echo watchlog_pic('hero-industry-atmosphere', '', 2000, 1125, 'cta-bg', '100vw'); ?>
  <div class="wrap center reveal">
    <h2>See what your cameras have been telling you.</h2>
    <p class="lead measure">Install in about ten minutes on a PC you already have. Keep your cameras
      and recorder — add WatchLog.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
