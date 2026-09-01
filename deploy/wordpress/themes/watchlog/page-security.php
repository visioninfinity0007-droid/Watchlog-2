<?php
/* Security: the serious page. Precise claims only (see PUBLIC_CLAIMS_MATRIX). */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Security</span></nav>
    <span class="eyebrow">Security</span>
    <h1>Security by architecture.</h1>
    <p class="lead measure">WatchLog is built so the sensitive parts never have to move. Your recorder is
      never exposed to the internet, its credentials stay on site, and only validated incident data
      leaves the building.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></div>
  </div>
  <div class="wrap-wide" style="margin-top:clamp(32px,4vw,52px)">
    <?php echo watchlog_pic('diagram-privacy','WatchLog privacy model: recorder stays private, credentials stay on site, connection is outbound only',1800,1000,'',' (max-width:1100px) 92vw, 1100px'); ?>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('arrow-out',20); ?> Outbound only</span>
        <h3>Nothing connects in.</h3>
        <p>The WatchLog Agent opens a connection <em>out</em> to WatchLog. There is no port forwarding,
          no VPN and no inbound firewall change. Your recorder is never reachable from the internet,
          and no public RTSP stream is opened.</p>
      </div>
      <div class="f-media"><div class="note-card">
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('check',20); ?> No port forwarding</li>
          <li><?php echo watchlog_icon('check',20); ?> No inbound firewall rules</li>
          <li><?php echo watchlog_icon('check',20); ?> No public RTSP</li>
          <li><?php echo watchlog_icon('check',20); ?> No VPN into your network</li>
        </ul></div></div>
    </div>

    <div class="feature flip">
      <div class="f-media"><div class="note-card">
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('lock',20); ?> Recorder login stays in a file on the site PC</li>
          <li><?php echo watchlog_icon('camera',20); ?> Recorded video stays on your recorder</li>
          <li><?php echo watchlog_icon('report',20); ?> Only event data + one still per incident sync</li>
        </ul></div></div>
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('lock',20); ?> Credentials &amp; footage</span>
        <h3>The sensitive parts never move.</h3>
        <p>Your recorder's admin username and password are entered once, at the site, and are never
          sent to WatchLog. Recorded video stays on your recorder. We never receive it and cannot
          browse it. Only validated event metadata and a single still per incident are synced.</p>
      </div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Access boundary</span>
      <h2>What WatchLog can and cannot access.</h2></div>
    <div class="access-cols">
      <div class="access-col card">
        <h3><?php echo watchlog_icon('check',22); ?> WatchLog can access</h3>
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('check',20); ?> Event records: time, camera and event type</li>
          <li><?php echo watchlog_icon('check',20); ?> One still image per incident that passed the on-site filter</li>
          <li><?php echo watchlog_icon('check',20); ?> Health signals: when each site and camera was last heard from</li>
          <li><?php echo watchlog_icon('check',20); ?> Your account details and report recipients</li>
        </ul>
      </div>
      <div class="access-col card">
        <h3><?php echo watchlog_icon('shield-off',22); ?> WatchLog cannot access</h3>
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('shield-off',20); ?> Live camera feeds (no viewing, pan or zoom)</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> Your recorded footage archive</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> The recorder's credentials</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> Anything on your network beyond the events it is given</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('lock',24); ?><h3>Tenant isolation</h3>
        <p>Each customer's data is separated at the database level. That separation is verified by an
          automated test that must pass before every release.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Data retention</h3>
        <p>Incident stills are kept by your plan (7, 30 or 90 days), then deleted. Event records
          without images are kept while your account is open.</p></div>
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Account security</h3>
        <p>Only people you invite can see your data, each with a role: owner, admin or read-only.
          Traffic to WatchLog is over HTTPS.</p></div>
    </div>
    <div class="note-card" style="margin-top:28px">
      <strong>Plain about what we don't claim.</strong> WatchLog does not do facial recognition, and we
      make no certification claims (such as SOC&nbsp;2 or ISO) we do not hold. We would rather state the
      boundary than imply one.
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Private by design. See it for yourself.</h2>
    <p class="lead measure">Install in about ten minutes on a PC you already have.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
