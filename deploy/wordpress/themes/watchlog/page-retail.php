<?php
/* Solution: Retail. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <?php echo watchlog_pic('solution-retail','',1800,1200,'page-hero-bg'); ?>
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><a href="<?php echo watchlog_url('solutions'); ?>">Solutions</a><span class="sep">/</span><span>Retail</span></nav>
      <span class="eyebrow">Retail</span>
      <h1>One view across every branch.</h1>
      <p class="lead">Understand every store without calling every store. WatchLog turns each branch's night of
        events into a short morning read, and head office sees the pattern.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('solution-retail','A retail store interior',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What matters here</span><h2>The cameras that earn their keep.</h2></div>
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('store',24); ?><h3>Branches</h3><p>Each store reports on its own, so one branch never hides behind another's numbers.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Opening &amp; closing</h3><p>The first and last events of the day show when each branch opened and shut.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After-hours</h3><p>Movement in a closed store is the number most branches read first each morning.</p></div>
      <div class="card"><?php echo watchlog_icon('camera',24); ?><h3>Vehicle &amp; foot activity</h3><p>People, cars and motorcycles are kept; headlights and weather are filtered out.</p></div>
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Per-branch recipients</h3><p>Each branch's report reaches the person who runs it, not one shared inbox.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Camera health</h3><p>A blind aisle is found the day you need it, unless a stopped camera is flagged sooner.</p></div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap feature narrow-media">
    <div class="f-copy">
      <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Every location</span>
      <h3>Head office sees the pattern.</h3>
      <p>Every branch reports the same way, so one screen shows which stores were quiet, which were
        busy after hours, and where a camera went dark, without a call to any of them.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('platform'); ?>">See the platform <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="f-media"><?php echo watchlog_shot('product-platform-overview','A WatchLog platform overview across retail branches',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Every branch, on one morning read.</h2>
    <p class="lead measure">Works with the recorder you already own. Install in about ten minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
