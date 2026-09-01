<?php
/* Platform: the strongest product page after the homepage. Real captures. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:54rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Platform</span></nav>
    <span class="eyebrow">The platform</span>
    <h1>One place to understand every site.</h1>
    <p class="lead measure">Incidents, site health, after-hours activity and a daily report, whether for one site
      or a hundred, in a portal scoped to your account and no one else's.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></div>
  </div>
  <div class="wrap-wide glow" style="margin-top:clamp(28px,4vw,48px)">
    <?php echo watchlog_shot('product-platform-overview','WatchLog Overview: sites, incidents, after-hours activity and site health',1900,1150,'(max-width:1200px) 92vw, 1100px', true, true); ?>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('camera',20); ?> Incident review</span>
        <h3>From motion to something worth reviewing.</h3>
        <p>Every kept event carries a still, filtered on site so noise never reaches you. Filter the
          history by site, camera and type, and open any incident to see the moment it happened.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('incidents'); ?>">Explore incidents <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
      <div class="f-media"><?php echo watchlog_shot('product-incidents','WatchLog Incidents with filters and a person incident selected',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
    </div>

    <div class="feature flip">
      <div class="f-media"><?php echo watchlog_shot('product-site-health','WatchLog Site Health with a healthy site and a camera fault',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('alert',20); ?> Site health</span>
        <h3>Know when something goes quiet.</h3>
        <p>Cameras that have gone silent, recorder faults, and whether the site is reporting at all are
          surfaced early, so a blind spot isn't discovered on the day you need the footage.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('site-health'); ?>">See site health <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
    </div>

    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('report',20); ?> Reporting</span>
        <h3>The useful part, every morning.</h3>
        <p>A daily summary to the right people on WhatsApp or email, in each site's local time, with a
          full delivery history kept in the portal.</p>
        <a class="arrow-link" href="<?php echo watchlog_url('reporting'); ?>">See reporting <?php echo watchlog_icon('arrow-right',18); ?></a>
      </div>
      <div class="f-media"><?php echo watchlog_shot('product-reports','WatchLog Reports with delivery history and recipients',1500,1000,'(max-width:900px) 92vw, 55vw',true); ?></div>
    </div>
  </div>
</section>

<section class="dark field">
  <div class="wrap split">
    <div>
      <span class="eyebrow">Multi-site &amp; teams</span>
      <h2>Built to scale past one location.</h2>
      <p>Head office sees the pattern across every site; each site's report goes to the person who runs
        it. Invite your team with roles for owner, admin and read-only, and set per-site recipients.</p>
      <ul class="ticks light-ticks">
        <li><?php echo watchlog_icon('sites',20); ?> One overview across every location</li>
        <li><?php echo watchlog_icon('people',20); ?> Owner, admin and read-only roles</li>
        <li><?php echo watchlog_icon('report',20); ?> Per-site recipients and channels</li>
      </ul>
    </div>
    <div><?php echo watchlog_shot('product-team','WatchLog Team page with owner, admin and viewer roles',1500,1000,'(max-width:900px) 92vw, 48vw',true); ?></div>
  </div>
</section>

<section class="cloud">
  <div class="wrap split">
    <div><?php echo watchlog_shot('product-plan-billing','WatchLog plan and trial state, sandbox clearly identified',1500,1000,'(max-width:900px) 92vw, 48vw',true); ?></div>
    <div>
      <span class="eyebrow">Trial &amp; plan</span>
      <h2>Clear about where you stand.</h2>
      <p>Settings shows your trial or plan and whether reporting is active. When a trial ends, reporting
        pauses and the portal says so plainly. Your recorded events and history are always kept.</p>
      <a class="arrow-link" href="<?php echo watchlog_url('pricing'); ?>">View pricing <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
  </div>
</section>

<section class="dark navy">
  <div class="wrap-wide">
    <div class="sec-head center">
      <span class="eyebrow">Security boundary</span>
      <h2>The platform never reaches into your network.</h2>
      <p class="lead measure">Everything in the portal is built from event data and stills the site chose
        to send, all of it outward only. No live access to your cameras, no path back into your recorder.</p>
    </div>
    <div style="max-width:1100px;margin:0 auto"><?php echo watchlog_pic('diagram-privacy','The outbound-only privacy model behind the platform',1800,1000,'','(max-width:1100px) 92vw, 1100px'); ?></div>
    <div class="center" style="margin-top:32px"><a class="btn btn-ghost" href="<?php echo watchlog_url('security'); ?>">Explore security</a></div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>See your sites in one place.</h2>
    <p class="lead measure">Start free for 14 days. Keep your cameras and recorder.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
