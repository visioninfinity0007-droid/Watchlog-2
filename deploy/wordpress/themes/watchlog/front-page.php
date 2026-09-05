<?php
/**
 * Homepage: Video Analytics SaaS positioning with explicit product maturity.
 * Available, Pilot / Coming Soon and Custom Solution are never mixed silently.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>

<section class="hero dark field glow-field">
  <?php echo watchlog_pic('hero-industry-atmosphere','',2000,1125,'hero-bg','100vw',true); ?>
  <div class="wrap-wide hero-grid">
    <div class="hero-copy">
      <span class="eyebrow">Video Analytics &amp; CCTV Intelligence Platform</span>
      <h1>Make your existing cameras useful every day.</h1>
      <p class="hero-sub">Turn selected CCTV views into incident intelligence, configured operational analytics, Site Health and scheduled reporting across one or many locations, without exposing the recorder to the public internet.</p>
      <div class="cta-row">
        <a class="btn btn-primary btn-xl" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-xl js-scroll" href="#available">See what is available</a>
      </div>
      <ul class="chips">
        <li class="chip"><?php echo watchlog_icon('check',18); ?> Keep your existing CCTV</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> On-site intelligence</li>
        <li class="chip"><?php echo watchlog_icon('check',18); ?> 14-day trial</li>
      </ul>
    </div>
    <div class="hero-visual">
      <div class="hv-main frame"><?php echo watchlog_pic('product-platform-overview','WatchLog platform overview',1900,1200,'shot-img','(max-width:900px) 96vw, 640px',true); ?></div>
      <div class="hv-card hv-incident"><?php echo watchlog_pic('cctv-person','',1600,900,'hv-thumb','120px'); ?><div><span class="hv-k">Incident intelligence</span><b>Selected event kept</b><span class="hv-t">Site and camera context</span></div></div>
      <div class="hv-card hv-report"><span class="hv-ic"><?php echo watchlog_icon('report',18); ?></span><div><b>Operational report</b><span class="hv-t">Email, WhatsApp or both</span></div></div>
      <div class="hv-card hv-health"><span class="hv-dot"></span><div><b>Site Health</b><span class="hv-t">Connection and camera-system visibility</span></div></div>
    </div>
  </div>
</section>

<section class="dark navy proof s-xs">
  <div class="wrap-wide proof-grid">
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('camera',24); ?></span><div><b>Existing camera systems</b><span>Assess the recorder and the actual camera views before rollout</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('chart',24); ?></span><div><b>Configured analytics</b><span>Lines, zones, schedules and site-specific measurements</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('lock',24); ?></span><div><b>Local video handling</b><span>Recorded video stays on your recorder</span></div></div>
    <div class="proof-item"><span class="proof-ic"><?php echo watchlog_icon('sites',24); ?></span><div><b>SaaS operations</b><span>Customer portal, multi-site visibility and reporting</span></div></div>
  </div>
</section>

<section class="surface problem">
  <div class="wrap split split-5-7">
    <div class="reveal">
      <span class="eyebrow">The opportunity</span>
      <h2>CCTV can answer operational questions, not only record evidence.</h2>
      <p class="lead">The value starts with the question the site needs answered and whether the existing camera view can support it reliably.</p>
      <p>WatchLog adds a local intelligence layer to selected camera views, then brings approved incidents, measurements, health signals and reports into one SaaS account.</p>
      <div class="pb-contrast">
        <div class="pb-from"><span class="pb-tag">Traditional CCTV</span><b>Footage and event logs</b><span>reviewed after the fact</span></div>
        <span class="pb-arrow"><?php echo watchlog_icon('arrow-right',22); ?></span>
        <div class="pb-to"><span class="pb-tag">With WatchLog</span><b>Operational visibility</b><span>from configured camera views</span></div>
      </div>
    </div>
    <div class="reveal problem-media"><?php echo watchlog_pic('environment-recorder','A CCTV recorder at a customer site',1800,1200,'','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="dark field glow-field grid-bg explorer" id="available">
  <div class="wrap-wide">
    <div class="sec-head center reveal">
      <span class="eyebrow">Available today</span>
      <h2>One platform across camera intelligence, site health and reporting.</h2>
      <p class="lead measure">The current product combines incident review, Analytics Studio, Site Health, reporting, team access and multi-site visibility.</p>
    </div>
    <div class="grid g3 reveal">
      <div class="card"><span class="eyebrow">Available</span><h3>Incident intelligence</h3><p>Use current detector classes and on-site filtering to keep selected events for review with site and camera context.</p><a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">See incidents <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Analytics Studio</h3><p>Configure lines, zones, schedules and thresholds around the operational question each camera view needs to answer.</p><a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See analytics <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Site Health</h3><p>See whether the site, recorder and cameras are still reporting so blind spots are visible operationally.</p><a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See Site Health <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Scheduled reporting</h3><p>Send configured summaries by WhatsApp, email or both, with delivery history available in the portal.</p><a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Multi-site SaaS</h3><p>Bring multiple locations into one customer account while keeping site context and customer access separated.</p><a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See platform <?php echo watchlog_icon('arrow-right',16); ?></a></div>
      <div class="card"><span class="eyebrow">Available</span><h3>Team access</h3><p>Give customer users access through the portal while preserving platform support and audit boundaries.</p><a class="arrow-link" href="<?php echo watchlog_url('security'); ?>">See security <?php echo watchlog_icon('arrow-right',16); ?></a></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center reveal">
      <span class="eyebrow">Analytics Studio · Available</span>
      <h2>Turn selected camera views into business measurements.</h2>
      <p class="lead measure">Camera placement, lighting, occlusion and site conditions affect measurement quality. Each use case should be calibrated and accepted against the real site.</p>
    </div>
    <div class="grid g3 reveal">
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Visitor / people flow</h3><p>Configured people movement through entrances, exits and selected areas. No facial recognition.</p></div>
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Vehicle flow</h3><p>Configured vehicle entries, exits and movement. This is anonymous flow measurement, not ANPR or vehicle identity.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Boundary activity</h3><p>Track configured line and zone activity around gates, perimeters and operational boundaries.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Dwell / time in zone</h3><p>Measure how long activity remains in a configured area using adjustable dwell thresholds.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>After-hours activity</h3><p>Use schedules to separate expected operating activity from movement outside configured hours.</p></div>
      <div class="card"><?php echo watchlog_icon('chart',24); ?><h3>Checkout-zone activity</h3><p>Measure activity or occupancy around a configured checkout zone. Not exact transactions, sales or till reconciliation.</p></div>
    </div>
  </div>
</section>

<section class="surface report-sec">
  <div class="wrap split split-7-5">
    <div class="reveal report-media"><div class="report-frame frame"><?php echo watchlog_pic('product-analytics','WatchLog Analytics Studio',1500,1050,'shot-img','(max-width:900px) 92vw, 55vw'); ?></div></div>
    <div class="reveal">
      <span class="eyebrow">Camera-layout acceptance</span>
      <h2>Analytics starts with what the camera can actually see.</h2>
      <p>A queue, warehouse gate, factory floor and forecourt need different views and rules. During pilot setup, review camera purpose, field of view, placement, lighting and the operational question before relying on a metric.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('check',20); ?> Camera-specific rules and schedules</li>
        <li><?php echo watchlog_icon('camera',20); ?> Real-view calibration and acceptance</li>
        <li><?php echo watchlog_icon('report',20); ?> Analytics aggregates can feed reports</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('solutions'); ?>">Explore industry solutions <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<section class="light solutions-sec">
  <div class="wrap-wide">
    <div class="sec-head reveal"><span class="eyebrow">Industry solutions</span><h2>Configure WatchLog around the work happening at each site.</h2></div>
    <div class="sol-editorial reveal">
      <?php
      $sols=[
        ['solutions/quick-service-restaurants','solution-retail','Quick-Service Restaurants','Branch analytics, reporting and a Control Room pilot path.'],
        ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','Loading areas, vehicle flow, zones, dwell and custom stock workflows.'],
        ['solutions/manufacturing','solution-manufacturing','Manufacturing & Textiles','Gates, zones, dwell, shift visibility and custom operational integration.'],
        ['solutions/retail','solution-retail','Retail','Visitor flow, checkout-zone activity and branch reporting.'],
        ['solutions/fuel-forecourt','solution-office-commercial','Fuel & Forecourt','Configured analytics plus customer-specific operational workflows.'],
      ];
      foreach ($sols as $i=>$s){ printf('<a class="sol-card%s" href="%s">%s<div class="sol-body"><span class="sol-ind">%s</span><p>%s</p><span class="sol-more">Explore %s %s</span></div></a>', $i===0?' sol-lead':'', watchlog_url($s[0]), watchlog_pic($s[1],$s[2],1800,1200,'sol-img',$i===0?'(max-width:900px) 92vw, 50vw':'(max-width:900px) 92vw, 25vw'), esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])), watchlog_icon('arrow-right',16)); }
      ?>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Pilot / Coming Soon</span><h2>Roadmap is labelled as roadmap.</h2><p class="lead measure">These directions come from the broader product plan. They are not silently presented as finished standard-product capability.</p></div>
    <div class="grid g3 reveal">
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Control Room</h3><p>A central operational workspace for multi-site oversight, camera and site status, event review and collective reporting is planned for pilot work. The current product does not provide a live video wall.</p></div>
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Fire &amp; smoke research</h3><p>Fire and smoke detection remain roadmap research and pilot areas until they are validated as production detectors.</p></div>
      <div class="card"><span class="eyebrow">Pilot / Coming Soon</span><h3>Extended object analytics</h3><p>Broader object detection, classification and tracking can be explored beyond the current production detector classes through controlled pilots.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Connect WatchLog to the systems that run the operation.</h2>
      <p>POS, Shopify, attendance machines, CRM and sales-pipeline systems can be scoped as customer-specific integration work. No catalogue of finished connectors is advertised today.</p>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('integrations'); ?>">Explore integrations</a><a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Discuss a custom solution</a></div>
    </div>
    <div class="note-card">
      <h3>Current product, pilot or custom?</h3>
      <p><strong>Available</strong> means it belongs to the current standard product. <strong>Pilot / Coming Soon</strong> means validation or productization is still required. <strong>Custom Solution</strong> means a customer-specific scope must be agreed before implementation.</p>
    </div>
  </div>
</section>

<section class="dark navy glow-field trust">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Security</span><h2>The recorder stays private.</h2><p class="lead measure">The site software connects outward. Recorded video stays on your recorder and recorder credentials remain on the site PC.</p></div>
    <div class="trust-grid reveal">
      <div class="trust-item"><?php echo watchlog_icon('shield',24); ?><b>No public recorder exposure</b><span>The architecture does not require opening inbound public access to the recorder.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('lock',24); ?><b>Protected credentials</b><span>On supported Windows installs, the recorder password uses machine-scoped protected storage.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('camera',24); ?><b>Local video boundary</b><span>The current product does not provide remote recorded-video archive browsing.</span></div>
      <div class="trust-item"><?php echo watchlog_icon('check',24); ?><b>Customer separation</b><span>Customer access is tenant-scoped and platform support actions preserve admin identity and audit context.</span></div>
    </div>
    <div class="center reveal" style="margin-top:36px"><a class="btn btn-ghost" href="<?php echo watchlog_url('security'); ?>">Explore security</a></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head center reveal"><span class="eyebrow">Pricing</span><h2>Start with a standard SaaS plan.</h2></div>
    <div class="grid g3 price-grid reveal">
      <div class="price-card"><h3>Starter</h3><p class="price"><span>PKR</span> 6,000<small>per site / month</small></p><p>Up to 8 cameras with the current Starter plan.</p><a class="btn btn-secondary" href="<?php echo $signup; ?>">Start free</a></div>
      <div class="price-card featured"><span class="price-tag">Most popular</span><h3>Growth</h3><p class="price"><span>PKR</span> 12,000<small>per site / month</small></p><p>Up to 24 cameras with the current Growth plan.</p><a class="btn btn-primary" href="<?php echo $signup; ?>">Start free</a></div>
      <div class="price-card"><h3>Enterprise</h3><p class="price price-talk">Talk to us</p><p>Multi-site, pilot or commercial scopes that need a quoted plan.</p><a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Talk to us</a></div>
    </div>
    <p class="center" style="margin-top:24px"><a href="<?php echo watchlog_url('pricing'); ?>">Full pricing</a> · <a href="<?php echo watchlog_url('refund-cancellation-service-delivery'); ?>">Refund, cancellation &amp; service delivery</a> · <a href="<?php echo watchlog_url('faq'); ?>">FAQ</a></p>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Start with the camera system you already have.</h2>
    <p class="lead measure">Then validate the camera views against the operational questions you actually need answered.</p>
    <div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Talk to WatchLog</a></div>
  </div>
</section>
<?php get_footer(); ?>
