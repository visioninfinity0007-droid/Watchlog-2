<?php
/* Solutions index: sector outcomes mapped to current product capability. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
$sols = [
  ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','After-hours activity, loading-bay movement, vehicle flow, boundaries and camera health.'],
  ['solutions/retail','solution-retail','Retail','Branch visibility, visitor flow, checkout-zone activity, after-hours movement and daily reporting.'],
  ['solutions/manufacturing','solution-manufacturing','Manufacturing','Visibility across shifts, gates and configured zones, plus dwell and site-health signals.'],
  ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Configured people flow, gates, boundaries, after-hours activity and camera health across buildings.'],
  ['solutions/offices','solution-office-commercial','Offices & Commercial','Visitor flow, after-hours activity, camera health and a daily operational summary.'],
];
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Solutions</span></nav>
    <span class="eyebrow">Solutions</span>
    <h1>Use the cameras you already own for more than evidence.</h1>
    <p class="lead measure">WatchLog combines validated incidents, site health, daily reporting and configured
      business measurements. The exact rules depend on the site, the camera view and the operational question.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Available</span>
      <h2>Choose the measurements that match the site.</h2>
      <p class="lead measure">Visitor flow, vehicle flow, boundary and zone activity, dwell / time in zone,
        after-hours activity and checkout-zone activity are available through configured Analytics Studio rules.
        Field conditions affect measurement quality.</p>
    </div>
    <div class="grid g3">
      <div class="card"><h3>Flow</h3><p>Configured people and vehicle movement through entrances, exits and selected boundaries.</p></div>
      <div class="card"><h3>Zones &amp; dwell</h3><p>Measure activity and time spent inside defined operational areas without facial recognition.</p></div>
      <div class="card"><h3>Schedules</h3><p>Separate expected activity from movement that happens outside configured operating hours.</p></div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap-wide">
    <div class="sol-grid">
      <?php foreach ($sols as $s) {
        printf('<a class="sol-tile" href="%s">%s<div class="sol-body"><span class="eyebrow">Available capability</span><h3>%s</h3><p>%s</p>'
          .'<span class="sol-more">Explore %s</span></div></a>',
          watchlog_url($s[0]),
          watchlog_pic($s[1], $s[2], 1800, 1200, 'sol-img', '(max-width:640px) 92vw, (max-width:1000px) 46vw, 30vw'),
          esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])));
      } ?>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap">
    <div class="sec-head center">
      <span class="eyebrow">Broader vision</span>
      <h2>Roadmap work stays visibly separate from what ships today.</h2>
    </div>
    <div class="grid g3">
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Control Room</h3>
        <p>A dedicated control-room module is planned. Current WatchLog does not provide a live video wall or saved multi-camera layouts.</p></div>
      <div class="card"><span class="eyebrow">Coming Soon</span><h3>Fire &amp; smoke research</h3>
        <p>Fire and smoke detection may be explored through pilots, but neither is an available standard-product detector today.</p></div>
      <div class="card"><span class="eyebrow">Custom Solution</span><h3>POS, attendance &amp; CRM</h3>
        <p>These integrations can be scoped for a customer implementation. They are not prebuilt connectors in the current product.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Not sure where you fit?</span>
      <h2>Start with the operational question, not the feature list.</h2>
      <p>Show us the recorder, the camera view and what the team needs to understand. We can tell you which
        current WatchLog rules fit, what needs calibration and what would require custom implementation.</p>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="note-card">
      <ul class="ticks" style="margin:0">
        <li><?php echo watchlog_icon('camera',20); ?> Keep the CCTV you already own</li>
        <li><?php echo watchlog_icon('lock',20); ?> Recorder never exposed to the public internet</li>
        <li><?php echo watchlog_icon('chart',20); ?> Configure analytics to the site and camera view</li>
        <li><?php echo watchlog_icon('sites',20); ?> Bring multiple locations into one operational view</li>
      </ul>
    </div>
  </div>
</section>
<?php get_footer(); ?>
