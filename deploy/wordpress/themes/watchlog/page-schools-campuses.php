<?php
/* Solution: Schools & Campuses. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-school-campus','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Schools &amp; Campuses</span></nav>
      <span class="eyebrow">Schools &amp; Campuses</span>
      <h1>Know what moved after hours.</h1>
      <p class="lead">A campus is quiet by design at night. That makes after-hours movement across gates, boundaries
        and several buildings worth a look. WatchLog turns the night's events into a short morning read.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-school-campus','A school campus courtyard',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What matters here</span><h2>The cameras that earn their keep.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('sites',24); ?><h3>Gates</h3><p>Entrances are watched for movement in the hours the campus is closed.</p></div>
      <div class="card"><?php echo watchlog_icon('shield',24); ?><h3>Boundaries</h3><p>Fence lines and perimeter paths stay in view when the grounds should be empty.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After-hours movement</h3><p>The after-hours count is the number most campuses read first each morning.</p></div>
      <div class="card"><?php echo watchlog_icon('building',24); ?><h3>Multiple buildings</h3><p>Blocks and outbuildings each report in, so no corner of the campus is left out.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Camera health</h3><p>A camera that stops overnight is flagged, so a blind spot isn't found only when you need it.</p></div>
      <div class="card"><?php echo watchlog_icon('report',24); ?><h3>Per-site reports</h3><p>Each site's report goes to the person who runs it; the group sees the pattern.</p></div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap feature narrow-media">
    <div class="f-copy">
      <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Worth reviewing</span>
      <h3>Filtered to what matters.</h3>
      <p>Most of a quiet night is nothing. WatchLog keeps only what's worth a second look, by camera
        and time, so a morning review takes minutes, not hours of footage.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">Explore incidents <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="f-media"><?php echo watchlog_shot('product-incidents','A WatchLog incidents list filtered for a campus',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Quiet campus, clear mornings.</h2>
    <p class="lead measure">Works with the recorder you already own. Install in about ten minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
