<?php
/* Solution: Warehouses & Logistics. Reference pattern for the other four. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-warehouse','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Warehouses &amp; Logistics</span></nav>
      <span class="eyebrow">Warehouses &amp; Logistics</span>
      <h1>See what happened after the shift ended.</h1>
      <p class="lead">Large perimeters, few people after hours, and a loading bay that matters. WatchLog
        turns a night of camera events into a short morning read, and tells you if a camera went dark.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-warehouse','A logistics warehouse yard at dusk',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What matters here</span><h2>The cameras that earn their keep.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('box',24); ?><h3>Loading bays</h3><p>Activity at the bay after hours is usually the first thing worth a look in the morning.</p></div>
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Perimeters</h3><p>Long fence lines and gates, watched for movement when the yard should be empty.</p></div>
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Vehicle activity</h3><p>Cars and motorcycles are kept; rain and headlights sweeping a wall are filtered out.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After-hours movement</h3><p>The after-hours count is the number most yards read first each morning.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Camera health</h3><p>A blind spot on a loading bay is found the day you need it, unless you're told sooner.</p></div>
      <div class="card"><?php echo watchlog_icon('report',24); ?><h3>Per-site reports</h3><p>Each site's report goes to the person who runs it; head office sees the pattern.</p></div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap feature narrow-media">
    <div class="f-copy">
      <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Every morning</span>
      <h3>A night of events, in one read.</h3>
      <p>Instead of scrubbing hours of footage, you get counts by camera and type, the after-hours
        number, first and last event times, and anything that went quiet, all in your site's local time.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="f-media"><?php echo watchlog_shot('product-reports','A WatchLog daily report for a warehouse site',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Put your night shift on the record.</h2>
    <p class="lead measure">Works with the recorder you already own. Install in about ten minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
