<?php
/* Solution: Manufacturing. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-manufacturing','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Manufacturing</span></nav>
      <span class="eyebrow">Manufacturing</span>
      <h1>Visibility across every shift.</h1>
      <p class="lead">Shift changes, gates and vehicle movement, and the areas you watch closely. WatchLog turns a
        night of camera events into a morning read, where a camera fault matters as much as an incident.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-manufacturing','A manufacturing plant floor',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What matters here</span><h2>The cameras that earn their keep.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Shift changes</h3><p>The events around each handover show when a shift actually started and ended.</p></div>
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Gates &amp; entries</h3><p>Gates and entry points are watched for movement when the site should be still.</p></div>
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Vehicle movement</h3><p>Cars and motorcycles at the gates are kept; rain and headlights are filtered out.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Critical areas</h3><p>The areas you watch most are surfaced first, not buried in the night's counts.</p></div>
      <div class="card"><?php echo watchlog_icon('recorder',24); ?><h3>Camera faults</h3><p>A camera that drops off is flagged the night it happens, not the day you need it.</p></div>
      <div class="card"><?php echo watchlog_icon('check',24); ?><h3>Site health</h3><p>One status shows every camera reported in, or which ones went quiet.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap feature narrow-media">
    <div class="f-copy">
      <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Nothing goes quiet</span>
      <h3>Know when a camera stops.</h3>
      <p>A camera that stops sending events is easy to miss for days. WatchLog checks that every camera
        reported in, and tells you the moment one goes quiet, before you go looking for the footage.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See site health <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="f-media"><?php echo watchlog_shot('product-site-health','A WatchLog site-health view for a manufacturing site',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Keep eyes on every shift.</h2>
    <p class="lead measure">Works with the recorder you already own. Install in about ten minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
