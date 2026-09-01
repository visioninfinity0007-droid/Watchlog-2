<?php
/* Reporting: daily report, channels, delivery history. Plain language. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Reporting</span></nav>
      <span class="eyebrow">Reporting</span>
      <h1>Wake up to the useful part.</h1>
      <p class="lead">Each morning, the right people get a short summary of what happened, not a feed to
        watch, not an inbox to clear. One read, then on with the day.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('platform'); ?>">See the platform</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_shot('product-reports','WatchLog Reports: a daily report with delivery history and recipient channels',1500,1050,'(max-width:900px) 92vw, 52vw',true); ?></div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">What's in it</span><h2>The whole night, in a glance.</h2></div>
    <div class="grid g4">
      <div class="card"><?php echo watchlog_icon('chart',24); ?><h3>By camera &amp; type</h3><p>Counts per camera and by kind of incident.</p></div>
      <div class="card"><?php echo watchlog_icon('moon',24); ?><h3>After hours</h3><p>How many happened when the site should be quiet.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>First &amp; last</h3><p>When activity started and stopped, in the site's local time.</p></div>
      <div class="card"><?php echo watchlog_icon('alert',24); ?><h3>Anything quiet</h3><p>Cameras or sites that stopped reporting.</p></div>
    </div>
  </div>
</section>

<section class="dark field glow-field grid-bg">
  <div class="wrap-wide">
    <div class="sec-head center"><span class="eyebrow">How it reaches you</span>
      <h2>Composed, then delivered once.</h2></div>
    <div style="max-width:920px;margin:0 auto"><?php echo watchlog_flow([
      ['layers','Incidents','Across your sites'],
      ['report','Daily summary','Counts, after-hours, faults'],
      ['whatsapp','Delivered','WhatsApp or email, 07:00',true],
    ], ['aria'=>'From incidents to a composed daily summary to delivery on WhatsApp or email']); ?></div>
    <div class="grid g3" style="margin-top:36px">
      <div class="card"><?php echo watchlog_icon('whatsapp',24); ?><h3>WhatsApp, email, or both</h3><p>Choose a channel per recipient. WhatsApp is a first-class channel, not an afterthought.</p></div>
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>The right people</h3><p>Per-site recipients, so the branch manager gets their branch, and head office gets the overview.</p></div>
      <div class="card"><?php echo watchlog_icon('report',24); ?><h3>Never sent twice</h3><p>The same day's report is never duplicated, and every send is kept in the portal's delivery history.</p></div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap split">
    <div>
      <span class="eyebrow">On your clock</span>
      <h2>In each site's local time.</h2>
      <p>A site in one city and a site in another each get counts in their own timezone, so "after hours"
        means what it should. The report lands in the morning, wherever the site is.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See site health <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="note-card">
      <h3 style="margin-top:0">A note on live sending</h3>
      <p style="margin-bottom:0">WatchLog is a daily reporting service, not a live alerting one. It tells
        you what happened, clearly, once a day. It does not watch in real time or dispatch a response.</p>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Get tomorrow's report.</h2>
    <p class="lead measure">Set your recipients in minutes. Fourteen days free, no card.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('pricing'); ?>">View pricing</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
