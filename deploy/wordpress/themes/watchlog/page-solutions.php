<?php
/* Solutions index: editorial, image-led. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
$sols = [
  ['solutions/warehouses-logistics','solution-warehouse','Warehouses & Logistics','See what happened after the shift ended: loading bays, perimeters and after-hours movement.'],
  ['solutions/retail','solution-retail','Retail','Understand every branch without calling every branch, with a report per store.'],
  ['solutions/manufacturing','solution-manufacturing','Manufacturing','Visibility across shifts, gates and critical areas, plus camera faults, fast.'],
  ['solutions/schools-campuses','solution-school-campus','Schools & Campuses','Know what moved after hours across gates, boundaries and multiple buildings.'],
  ['solutions/offices','solution-office-commercial','Offices & Commercial','Daily confirmation that nothing happened, and immediate word when a camera stops.'],
];
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:52rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Solutions</span></nav>
    <span class="eyebrow">Solutions</span>
    <h1>WatchLog for the sites you already operate.</h1>
    <p class="lead measure">The product is the same everywhere: validated incidents, site health and a
      daily report. What changes is which cameras carry the value. Here's how it lands by sector.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
  </div>
</section>

<section class="light">
  <div class="wrap-wide">
    <div class="sol-grid">
      <?php foreach ($sols as $s) {
        printf('<a class="sol-tile" href="%s">%s<div class="sol-body"><h3>%s</h3><p>%s</p>'
          .'<span class="sol-more">Explore %s</span></div></a>',
          watchlog_url($s[0]),
          watchlog_pic($s[1], $s[2], 1800, 1200, 'sol-img', '(max-width:640px) 92vw, (max-width:1000px) 46vw, 30vw'),
          esc_html($s[2]), esc_html($s[3]), esc_html(strtolower($s[2])));
      } ?>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Not sure where you fit?</span>
      <h2>If you own cameras, WatchLog has a job to do.</h2>
      <p>The common thread across every sector is the same: cameras that already record, and no one with
        the time to watch them. WatchLog reads what they saw and tells you the useful part.</p>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="note-card">
      <ul class="ticks" style="margin:0">
        <li><?php echo watchlog_icon('camera',20); ?> Works with the CCTV you already own</li>
        <li><?php echo watchlog_icon('lock',20); ?> Recorder never exposed to the internet</li>
        <li><?php echo watchlog_icon('report',20); ?> One daily report per site, to the right people</li>
        <li><?php echo watchlog_icon('sites',20); ?> One view across every location</li>
      </ul>
    </div>
  </div>
</section>
<?php get_footer(); ?>
