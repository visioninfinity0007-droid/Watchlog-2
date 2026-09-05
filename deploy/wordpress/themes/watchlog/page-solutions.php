<?php
/* Solutions index: current capability first, pilots and custom work clearly separated. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
$sols = [
  ['solutions/quick-service-restaurants','solution-retail','Quick-Service Restaurants','Branch analytics, people flow, configured queue and checkout-zone activity, reporting and the upcoming Control Room pilot.','Pilot + Available'],
  ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','People and vehicle flow, loading areas, boundaries, dwell, after-hours activity and site reporting.','Available + Custom'],
  ['solutions/retail','solution-retail','Retail','Visitor flow, checkout-zone activity, after-hours movement, site health and branch reporting.','Available'],
  ['solutions/manufacturing','solution-manufacturing','Manufacturing & Textiles','Gates, configured zones, dwell, people and vehicle flow, shift visibility and custom operational workflows.','Available + Custom'],
  ['solutions/fuel-forecourt','solution-office-commercial','Fuel & Forecourt','Configured forecourt activity, vehicle flow, site health and tailored operational reporting.','Custom Solution'],
  ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Configured people flow, gates, boundaries, after-hours activity and camera health across buildings.','Available'],
  ['solutions/offices','solution-office-commercial','Offices & Commercial','Visitor flow, after-hours activity, camera health and daily operational summaries.','Available'],
];
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:58rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Solutions</span></nav>
    <span class="eyebrow">Industry solutions</span>
    <h1>Make existing cameras useful for the work happening at each site.</h1>
    <p class="lead measure">WatchLog is a Video Analytics &amp; CCTV Intelligence platform. Start with the current analytics and reporting product, then scope pilot or custom workflows where the operational question needs more.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Discuss a solution</a></div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Available today</span>
      <h2>Configured analytics, site health and reporting.</h2>
      <p class="lead measure">Visitor / people flow, vehicle flow, boundary activity, Dwell / time in zone, After-hours activity and Checkout-zone activity are available through configured Analytics Studio rules. Camera placement, lighting and site conditions affect measurement quality.</p>
    </div>
    <div class="grid g3">
      <div class="card"><h3>People and vehicle flow</h3><p>Measure configured movement through entrances, exits, gates and operational boundaries without facial recognition or ANPR.</p></div>
      <div class="card"><h3>Zones, dwell and schedules</h3><p>Measure activity and time spent in defined areas, then separate expected operating hours from after-hours movement.</p></div>
      <div class="card"><h3>Site and multi-site visibility</h3><p>Combine camera-system health, incidents, analytics and scheduled reporting across one or many locations.</p></div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap-wide">
    <div class="sec-head"><span class="eyebrow">Verticals</span><h2>Start from the business question.</h2></div>
    <div class="sol-grid">
      <?php foreach ($sols as $s) {
        printf('<a class="sol-tile" href="%s">%s<div class="sol-body"><span class="eyebrow">%s</span><h3>%s</h3><p>%s</p>'
          .'<span class="sol-more">Explore %s</span></div></a>',
          watchlog_url($s[0]),
          watchlog_pic($s[1], $s[2], 1800, 1200, 'sol-img', '(max-width:640px) 92vw, (max-width:1000px) 46vw, 30vw'),
          esc_html($s[4]), esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])));
      } ?>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Pilot / Coming Soon</span>
      <h2>The broader product direction is visible, but not presented as finished.</h2>
      <p class="lead measure">The meeting roadmap includes Control Room, fire and smoke research, broader object analytics and industry-specific workflows. These remain pilots or roadmap items until validated.</p>
    </div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Control Room</h3>
        <p>A dedicated operational workspace for multi-site oversight, camera and site status, event review and reporting. WatchLog does not provide a live video wall today.</p></div>
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Fire &amp; smoke research</h3>
        <p>Fire and smoke detection are being treated as pilot research areas, not available standard-product detectors.</p></div>
      <div class="card"><span class="eyebrow">Pilot / Coming Soon</span><h3>Extended object analytics</h3>
        <p>Broader object detection, classification and tracking can be explored in controlled pilots beyond the current production detector classes.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Custom Solution</span>
      <h2>Connect camera intelligence to the systems your operation already uses.</h2>
      <p>POS, Shopify, attendance machines, CRM and sales-pipeline systems can be scoped as customer-specific integration work. There is no catalogue of finished connectors today.</p>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('integrations'); ?>">Explore integrations</a>
        <a class="btn btn-secondary" href="<?php echo watchlog_url('contact'); ?>">Discuss a custom solution</a></div>
    </div>
    <div class="note-card">
      <h3>Analytics starts with the camera view.</h3>
      <p>Good analytics depends on what the camera can actually see. During pilot setup, WatchLog reviews camera purpose, field of view, placement, lighting and the operational question. If a view cannot support a reliable measurement, that limitation should be identified rather than hidden.</p>
    </div>
  </div>
</section>
<?php get_footer(); ?>
