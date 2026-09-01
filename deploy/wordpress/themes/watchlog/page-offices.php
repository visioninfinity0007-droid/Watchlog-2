<?php
/* Solution: Offices & Commercial. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-office-commercial','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Offices &amp; Commercial</span></nav>
      <span class="eyebrow">Offices &amp; Commercial</span>
      <h1>Daily visibility without watching screens.</h1>
      <p class="lead">Smaller sites run a handful of cameras, so the value isn't hours of footage. It's knowing nothing
        happened, and being told the moment a camera stops. It works across every office you run.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-office-commercial','A commercial office interior',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What matters here</span><h2>The cameras that earn their keep.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Small camera counts</h3><p>A handful of cameras still adds up to a night of events worth summarising.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After-hours movement</h3><p>Movement after the office closes is the first thing worth a look in the morning.</p></div>
      <div class="card"><?php echo watchlog_icon('check',24); ?><h3>Daily confirmation</h3><p>On a quiet night the report simply confirms that nothing happened.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Camera health</h3><p>A camera that stops overnight is flagged, so a blind spot isn't found only when you need it.</p></div>
      <div class="card"><?php echo watchlog_icon('building',24); ?><h3>Multiple offices</h3><p>Each office reports on its own, so one location never hides behind another.</p></div>
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Team roles</h3><p>Reports reach the right person per office, with access set by role.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap feature narrow-media">
    <div class="f-copy">
      <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Every morning</span>
      <h3>Confirmation nothing happened.</h3>
      <p>On most mornings the report says nothing happened, and that's the point. When something did,
        or a camera went quiet, it's the first thing you see.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="f-media"><?php echo watchlog_shot('product-reports','A WatchLog daily report for an office',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Peace of mind, delivered daily.</h2>
    <p class="lead measure">Works with the recorder you already own. Install in about ten minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
