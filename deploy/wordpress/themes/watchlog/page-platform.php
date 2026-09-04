<?php
/* Platform: current product first, roadmap clearly separated. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Platform</span></nav>
    <span class="eyebrow">Video analytics &amp; CCTV intelligence</span>
    <h1>Turn existing cameras into operational visibility.</h1>
    <p class="lead measure">WatchLog combines incident review, camera health, daily reporting and configured
      business measurements across one or many sites. Recorded video stays on your recorder and the Agent
      connects outward only.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></div>
  </div>
  <div class="wrap-wide glow" style="margin-top:clamp(28px,4vw,48px)">
    <?php echo watchlog_shot('product-platform-overview','WatchLog Overview: sites, incidents, after-hours activity and site health',1900,1150,'(max-width:1200px) 92vw, 1100px', true, true); ?>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Available</span>
      <h2>Security visibility plus business measurements.</h2>
      <p class="lead measure">Analytics Studio turns configured cameras into measurements. Camera placement,
        lighting and site conditions affect measurement quality, so each deployment is calibrated to the job.</p>
    </div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Available</span><h3>Visitor &amp; people flow</h3>
        <p>Configured line and occupancy rules can measure people flow through entrances and defined areas without identifying faces.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Vehicle flow</h3>
        <p>Measure configured vehicle entries, exits and movement through selected boundaries. This is flow measurement, not ANPR or vehicle identity.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Boundary &amp; zone activity</h3>
        <p>Use lines and zones to record configured movement around gates, perimeters, loading areas and operational spaces.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Dwell / time in zone</h3>
        <p>Measure how long activity remains inside a configured zone using adjustable dwell thresholds.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>After-hours activity</h3>
        <p>Schedule-based rules separate expected daytime activity from movement that happens outside configured hours.</p></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Checkout-zone activity</h3>
        <p>Measure activity or occupancy around a configured checkout zone. WatchLog does not infer exact transactions, sales or till reconciliation from CCTV alone.</p></div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('camera',20); ?> Incident review</span>
        <h3>From motion to something worth reviewing.</h3>
        <p>Every kept event carries a still, filtered on site so common false alarms do not flood the portal.
          Filter history by site, camera and type, then open an incident to see the captured moment.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">Explore incidents <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
      <div class="f-media"><?php echo watchlog_shot('product-incidents','WatchLog Incidents with filters and a person incident selected',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
    </div>

    <div class="feature flip">
      <div class="f-media"><?php echo watchlog_shot('product-site-health','WatchLog Site Health with a healthy site and a camera fault',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('alert',20); ?> Site health</span>
        <h3>Know when something goes quiet.</h3>
        <p>Cameras that have gone silent, recorder faults and reporting gaps are surfaced early so a blind spot
          is not discovered on the day you need the footage.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See site health <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
    </div>

    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Reporting</span>
        <h3>The useful part, every morning.</h3>
        <p>A daily summary goes to the right people on WhatsApp, email or both, in each site's local time.
          Analytics Studio aggregates can be included alongside incidents and site-health information.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
      <div class="f-media"><?php echo watchlog_shot('product-reports','WatchLog Reports with delivery history and recipients',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Multi-site &amp; teams</span>
      <h2>Built to scale past one location.</h2>
      <p>Head office can see the pattern across sites while each site's report goes to the people who run it.
        Invite your team with owner, admin and read-only roles and set per-site recipients.</p>
      <ul class="ticks light-ticks">
        <li><?php echo watchlog_icon('sites',20); ?> One overview across every location</li>
        <li><?php echo watchlog_icon('people',20); ?> Owner, admin and read-only roles</li>
        <li><?php echo watchlog_icon('report',20); ?> Per-site recipients and channels</li>
      </ul>
    </div>
    <div><?php echo watchlog_shot('product-team','WatchLog Team page with owner, admin and viewer roles',1500,1000,'(max-width:900px) 92vw, 48vw',true); ?></div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Product direction</span>
      <h2>What comes next is labelled before it is sold.</h2>
      <p class="lead measure">The broader WatchLog vision is real, but these modules are not presented as current standard-product capabilities.</p>
    </div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Control Room</h3>
        <p>A dedicated control-room experience is planned. WatchLog does not currently offer a live video wall or saved multi-camera layouts.</p></div>
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Fire &amp; smoke pilots</h3>
        <p>Fire and smoke detection are roadmap research items, not available detection capabilities in the current product.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>Business-system integrations</h3>
        <p>POS, attendance, CRM and other integrations can be scoped as implementation work. WatchLog does not yet ship a catalogue of finished connectors.</p></div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap split">
    <div><?php echo watchlog_shot('product-plan-billing','WatchLog plan and trial state, sandbox clearly identified',1500,1000,'(max-width:900px) 92vw, 48vw',true); ?></div>
    <div>
      <span class="eyebrow">Trial &amp; plan</span>
      <h2>Clear about where you stand.</h2>
      <p>Settings shows your trial or plan and whether reporting is active. When a trial ends, reporting
        pauses and the portal says so plainly. Your recorded events and history are kept.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('pricing'); ?>">View pricing <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<section class="dark navy glow-field">
  <div class="wrap-wide">
    <div class="sec-head center">
      <span class="eyebrow">Security boundary</span>
      <h2>The platform never reaches into your network.</h2>
      <p class="lead measure">The Agent connects outward only. Recorded video stays on your recorder;
        WatchLog receives the event, analytics, health and still-image data needed to provide the configured service.</p>
    </div>
    <div style="max-width:940px;margin:0 auto"><?php echo watchlog_flow([
      ['lock','Recorder','Never publicly exposed'],
      ['pc','Credentials','Protected on the site PC'],
      ['arrow-out','Connection','Outbound only'],
      ['cloud','WatchLog','Operational data and stills',true],
    ], ['aria'=>'The outbound-only privacy model behind the platform']); ?></div>
    <div class="center" style="margin-top:32px"><a class="btn btn-ghost" href="<?php echo watchlog_url('security'); ?>">Explore security</a></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>See more value from the cameras you already own.</h2>
    <p class="lead measure">Start with one site and configure the measurements that matter there.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
