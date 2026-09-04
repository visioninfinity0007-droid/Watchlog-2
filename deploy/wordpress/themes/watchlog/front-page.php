<?php
/**
 * Homepage - WatchLog.
 * Current product capability is shown first. Roadmap items are explicitly labelled.
 * No em dashes in public copy.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>

<section class="hero dark field glow-field">
  <?php echo watchlog_pic('hero-industry-atmosphere','',2000,1125,'hero-bg','100vw',true); ?>
  <div class="wrap-wide hero-grid">
    <div class="hero-copy">
      <span class="eyebrow">Video analytics &amp; CCTV intelligence</span>
      <h1>Get more value from the cameras you already own.</h1>
      <p class="hero-sub">Turn existing CCTV into validated incidents, site-health visibility, daily reporting
        and configured business measurements, without exposing the recorder to the public internet.</p>
      <div class="cta-row">
        <a class="btn btn-primary btn-xl" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-xl js-scroll" href="#platform">See the platform</a>
      </div>
      <ul class="chips">
        <li class="chip"><?php echo watchlog_icon('check',18); ?> Keep your existing CCTV</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> On-site intelligence</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> 14-day trial</li>
      </ul>
    </div>
    <div class="hero-visual">
      <div class="hv-main frame"><?php echo watchlog_pic('product-platform-overview','WatchLog Overview dashboard',1900,1200,'shot-img','(max-width:900px) 96vw, 640px',true); ?></div>
      <div class="hv-card hv-incident">
        <?php echo watchlog_pic('cctv-person','',1600,900,'hv-thumb','120px'); ?>
        <div><span class="hv-k">Incident kept</span><b>Person, Main Gate</b><span class="hv-t">22:16 · after hours</span></div>
      </div>
      <div class="hv-card hv-report">
        <span class="hv-ic"><?php echo watchlog_icon('check',18); ?></span>
        <div><b>Daily report delivered</b><span class="hv-t">WhatsApp and email</span></div>
      </div>
      <div class="hv-card hv-health">
        <span class="hv-dot"></span><div><b>Camera health visible</b><span class="hv-t">Site by site</span></div>
      </div>
    </div>
  </div>
</section>

<section class="dark navy proof s-xs">
  <div class="wrap-wide proof-grid">
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('camera',24); ?></span><div><b>Existing recorder</b><span>Hikvision, Dahua and protocol-compatible ONVIF units can be assessed</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('check',24); ?></span><div><b>On-site processing</b><span>Incident filtering and analytics run on the site PC</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('lock',24); ?></span><div><b>Outbound only</b><span>No inbound connection to the recorder is required</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('report',24); ?></span><div><b>Operational output</b><span>Portal visibility plus WhatsApp and email reporting</span></div></div>
  </div>
</section>

<section class="surface problem">
  <div class="wrap split split-5-7">
    <div class="reveal">
      <span class="eyebrow">The gap</span>
      <h2>CCTV records what happened. WatchLog helps you use it.</h2>
      <p class="lead">Most camera systems are opened only after something has already gone wrong.</p>
      <p>WatchLog adds an intelligence layer to the recorder you already have, turning selected camera views
        into incidents, health signals, measurements and summaries that can be used every day.</p>
      <div class="pb-contrast">
        <div class="pb-from"><span class="pb-tag">Traditional CCTV</span><b>Hours of footage</b><span>searched after the fact</span></div>
        <span class="pb-arrow"><?php echo watchlog_icon('arrow-right',22); ?></span>
        <div class="pb-to"><span class="pb-tag">With WatchLog</span><b>Operational visibility</b><span>from the same cameras</span></div>
      </div>
    </div>
    <div class="reveal problem-media">
      <?php echo watchlog_pic('environment-recorder','A CCTV recorder on a shelf at a site',1800,1200,'','(max-width:900px) 92vw, 52vw'); ?>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg explorer" id="platform">
  <div class="wrap-wide">
    <div class="sec-head center reveal">
      <span class="eyebrow">Available</span>
      <h2>One platform across security and operations.</h2>
      <p class="lead measure">The current product combines incident review, Site Health, Analytics Studio,
        reporting, team access and multi-site visibility.</p>
    </div>
    <div class="grid g3 reveal">
      <div class="card"><span class="eyebrow">Available</span><h3>Incident intelligence</h3>
        <p>Filter common motion noise on the site PC and keep incidents that contain the current detector classes: person, car and motorcycle.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">See incidents <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Site Health</h3>
        <p>See cameras that have gone quiet, recorder faults and whether each site is reporting.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See Site Health <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Daily reporting</h3>
        <p>Send a daily summary by WhatsApp, email or both, with delivery history kept in the portal.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',16); ?></a></div>
    </div>
    <div class="center reveal" style="margin-top:34px">
      <a class="btn btn-ghost" href="<?php echo watchlog_url('platform'); ?>">Explore the full platform</a>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center reveal">
      <span class="eyebrow">Analytics Studio · Available</span>
      <h2>Turn selected camera views into business measurements.</h2>
      <p class="lead measure">Analytics Studio uses configured lines, zones and schedules. Camera placement,
        lighting and site conditions affect measurement quality, so each use case is calibrated to the site.</p>
    </div>
    <div class="grid g3 reveal">
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Visitor / people flow</h3>
        <p>Configured people movement through entrances, exits and selected areas. No facial recognition.</p></div>
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Vehicle flow</h3>
        <p>Configured vehicle entries, exits and movement. This is flow measurement, not ANPR or vehicle identity.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Boundary activity</h3>
        <p>Track configured line and zone activity around gates, perimeters and operational boundaries.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Dwell / time in zone</h3>
        <p>Measure how long activity remains in a configured area using adjustable dwell thresholds.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>After-hours activity</h3>
        <p>Use schedules to separate expected daytime activity from movement outside configured hours.</p></div>
      <div class="card"><?php echo watchlog_icon('chart',24); ?><h3>Checkout-zone activity</h3>
        <p>Measure activity or occupancy around a configured checkout zone. Not exact transactions, sales or till reconciliation.</p></div>
    </div>
  </div>
</section>

<section class="surface report-sec">
  <div class="wrap split split-7-5">
    <div class="reveal report-media">
      <div class="report-frame frame"><?php echo watchlog_pic('product-analytics','WatchLog Analytics Studio',1500,1050,'shot-img','(max-width:900px) 92vw, 55vw'); ?></div>
    </div>
    <div class="reveal">
      <span class="eyebrow">Configured, not generic</span>
      <h2>Measure the question the site actually has.</h2>
      <p>A retail entrance, a warehouse gate and a factory loading area need different rules. WatchLog lets
        the camera purpose and the operational question determine what is measured.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('check',20); ?> Camera-specific rules and schedules</li>
        <li><?php echo watchlog_icon('check',20); ?> Local measurement before upload</li>
        <li><?php echo watchlog_icon('report',20); ?> Analytics aggregates can feed daily reports</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See platform capabilities <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<section class="dark navy glow-field trust">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Security</span>
      <h2>The recorder stays private.</h2>
      <p class="lead measure">The Agent connects outward only. Recorded video stays on your recorder and recorder
        credentials remain on the site PC.</p></div>
    <div class="trust-grid reveal">
      <div class="trust-item"><?php echo watchlog_icon('shield',24); ?><b>No public exposure</b><span>Your recorder is never exposed to the public internet.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('lock',24); ?><b>Protected credentials</b><span>On supported Windows installs, the recorder password uses machine-scoped protected storage.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('camera',24); ?><b>Recorded video stays local</b><span>WatchLog does not provide live camera browsing or recorded-archive access.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('check',24); ?><b>Tenant isolation</b><span>Customer data is isolated in the database and covered by automated isolation tests.</span></div>
    </div>
    <div class="center reveal" style="margin-top:36px"><a class="btn btn-ghost" href="<?php echo watchlog_url('security'); ?>">Explore security</a></div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center reveal">
      <span class="eyebrow">Product direction</span>
      <h2>Roadmap is labelled as roadmap.</h2>
      <p class="lead measure">These are part of the broader vision, not features we present as available today.</p>
    </div>
    <div class="grid g3 reveal">
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Control Room</h3>
        <p>A dedicated control-room experience is planned. The current product does not offer a live video wall or saved multi-camera layouts.</p></div>
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Fire &amp; smoke research</h3>
        <p>Fire and smoke detection are roadmap research items, not available production detectors today.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>POS, attendance &amp; CRM</h3>
        <p>Custom integration work can be scoped for a customer. There is no catalogue of finished connectors yet.</p></div>
    </div>
  </div>
</section>

<section class="light solutions-sec">
  <div class="wrap-wide">
    <div class="sec-head reveal"><span class="eyebrow">Built for your sites</span>
      <h2>Configure WatchLog around the work happening there.</h2></div>
    <div class="sol-editorial reveal">
      <?php
      $sols=[
        ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','Loading bays, vehicle flow, boundaries and after-hours activity.'],
        ['solutions/retail','solution-retail','Retail','Visitor flow, checkout-zone activity and branch reporting.'],
        ['solutions/manufacturing','solution-manufacturing','Manufacturing','Gates, zones, dwell and shift visibility.'],
        ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Entrances, boundaries and after-hours activity.'],
        ['solutions/offices','solution-office-commercial','Offices & Commercial','Visitor flow, camera health and daily visibility.'],
      ];
      foreach ($sols as $i=>$s){ printf(
        '<a class="sol-card%s" href="%s">%s<div class="sol-body"><span class="sol-ind">%s</span><p>%s</p>'
        .'<span class="sol-more">Explore %s %s</span></div></a>',
        $i===0?' sol-lead':'', watchlog_url($s[0]),
        watchlog_pic($s[1],$s[2],1800,1200,'sol-img', $i===0?'(max-width:900px) 92vw, 50vw':'(max-width:900px) 92vw, 25vw'),
        esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])), watchlog_icon('arrow-right',16)); }
      ?>
    </div>
  </div>
</section>

<section class="surface compat-sec">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Compatibility</span>
      <h2>Keep the cameras. Keep the recorder. Add WatchLog.</h2></div>
    <div class="note-card reveal" style="max-width:900px;margin:0 auto">
      <div class="compat-brands">
        <span class="cb v"><?php echo watchlog_icon('check',15); ?> Dahua</span>
        <span class="cb p">Hikvision</span>
        <span class="cb p">HiLook</span>
        <span class="cb p">Imou</span>
        <span class="cb p">CP&nbsp;Plus</span>
        <span class="cb p">Uniview</span>
        <span class="cb p">Tiandy</span>
        <span class="cb p">ONVIF</span>
      </div>
      <p class="cb-legend"><span class="cb-key v"></span> Real-hardware experience exists, broad field validation ongoing
        <span class="cb-key p"></span> Protocol-compatible or simulator-validated, confirm your unit</p>
      <p class="center">Compatibility is confirmed against the actual recorder at install.</p>
      <div class="cta-row" style="justify-content:center"><a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('compatibility'); ?>">See compatibility</a></div>
    </div>
  </div>
</section>

<section class="surface-cool pricing-sec">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Pricing</span>
      <h2>Start with one site. Prove the value in 14 days.</h2>
      <p class="lead measure">No card for the trial. When a trial ends, reporting pauses and your events and history are kept.</p></div>
    <div class="grid g3 price-grid reveal">
      <div class="price-card" data-plan="starter">
        <h3>Starter</h3><p class="price"><span>PKR</span> 6,000<small>per site / month</small></p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('clock',18); ?> 7 days of stills</li>
          <li><?php echo watchlog_icon('report',18); ?> WhatsApp and email reports</li></ul>
        <a class="btn btn-secondary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card featured" data-plan="growth">
        <span class="price-tag">Most sites</span>
        <h3>Growth</h3><p class="price"><span>PKR</span> 12,000<small>per site / month</small></p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('clock',18); ?> 30 days of stills</li>
          <li><?php echo watchlog_icon('chart',18); ?> Analytics and site health</li></ul>
        <a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a>
      </div>
      <div class="price-card" data-plan="enterprise">
        <h3>Enterprise</h3><p class="price price-talk">Talk to us</p>
        <ul class="ticks" style="margin:0 0 1.4rem"><li><?php echo watchlog_icon('clock',18); ?> 90 days of stills</li>
          <li><?php echo watchlog_icon('sites',18); ?> Central multi-site view</li></ul>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a>
      </div>
    </div>
    <p class="center" style="margin-top:22px"><a class="arrow-link" href="<?php echo watchlog_url('pricing'); ?>">View full pricing <?php echo watchlog_icon('arrow-right',18); ?></a></p>
  </div>
</section>

<section class="dark field glow-field final-cta">
  <?php echo watchlog_pic('hero-industry-atmosphere','',2000,1125,'cta-bg','100vw'); ?>
  <div class="wrap center">
    <h2>See what your existing cameras can do next.</h2>
    <p class="lead measure">Start with one site, configure the right camera views and build from there.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-xl" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-xl" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>

<?php get_footer(); ?>
