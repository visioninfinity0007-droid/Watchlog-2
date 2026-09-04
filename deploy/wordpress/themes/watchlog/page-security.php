<?php
/* Security: precise architecture and data-boundary claims only. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Security</span></nav>
    <span class="eyebrow">Security</span>
    <h1>Security by architecture.</h1>
    <p class="lead measure">Your recorder is never exposed to the public internet. The Agent connects outward only,
      recorder credentials remain on the site PC, and recorded video stays on your recorder.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></div>
  </div>
  <div class="wrap-wide" style="margin-top:clamp(32px,4vw,52px)">
    <?php echo watchlog_flow([
      ['lock','Recorder','Never publicly exposed'],
      ['pc','Credentials','Protected on the site PC'],
      ['arrow-out','Connection','Outbound only'],
      ['cloud','WatchLog','Operational data and stills',true],
    ], ['aria'=>'Recorder stays private, credentials stay on site, connection is outbound only']); ?>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="feature narrow-media">
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('arrow-out',20); ?> Outbound only</span>
        <h3>Nothing connects in.</h3>
        <p>The WatchLog Agent opens connections <em>out</em> to WatchLog. There is no port forwarding,
          no VPN and no inbound firewall change. Your recorder is never reachable from the public internet,
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
          <li><?php echo watchlog_icon('lock',20); ?> Recorder password protected with Windows local-machine protected storage</li>
          <li><?php echo watchlog_icon('camera',20); ?> Recorded video stays on your recorder</li>
          <li><?php echo watchlog_icon('report',20); ?> WatchLog receives only the operational data and stills required for configured features</li>
        </ul></div></div>
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('lock',20); ?> Credentials &amp; footage</span>
        <h3>The sensitive parts stay on site.</h3>
        <p>Your recorder username and password are entered at the site and are never sent to WatchLog.
          On supported Windows installs, the recorder password is stored using machine-scoped Windows DPAPI
          with file access restricted to SYSTEM and local Administrators. Recorded video stays on the recorder.</p>
      </div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head center"><span class="eyebrow">Access boundary</span>
      <h2>What WatchLog can and cannot access.</h2></div>
    <div class="access-cols">
      <div class="access-col card">
        <h3><?php echo watchlog_icon('check',22); ?> WatchLog can access</h3>
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('check',20); ?> Event records such as time, camera and event type</li>
          <li><?php echo watchlog_icon('check',20); ?> Incident stills that passed the on-site filter</li>
          <li><?php echo watchlog_icon('check',20); ?> Configured Analytics Studio measurements</li>
          <li><?php echo watchlog_icon('check',20); ?> On-demand configuration stills used to set up camera rules</li>
          <li><?php echo watchlog_icon('check',20); ?> Health signals such as when each site and camera was last heard from</li>
          <li><?php echo watchlog_icon('check',20); ?> Your account details and report recipients</li>
        </ul>
      </div>
      <div class="access-col card">
        <h3><?php echo watchlog_icon('shield-off',22); ?> WatchLog cannot access</h3>
        <ul class="ticks" style="margin:0">
          <li><?php echo watchlog_icon('shield-off',20); ?> Live camera feeds through the WatchLog portal</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> Pan, tilt or zoom controls</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> Your recorded footage archive</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> The recorder's credentials</li>
          <li><?php echo watchlog_icon('shield-off',20); ?> A general path into your local network</li>
        </ul>
      </div>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="grid g3">
      <div class="card"><?php echo watchlog_icon('lock',24); ?><h3>Tenant isolation</h3>
        <p>Each customer's data is isolated in the database and covered by automated isolation tests.</p></div>
      <div class="card"><?php echo watchlog_icon('clock',24); ?><h3>Data retention</h3>
        <p>Incident stills are kept by your plan for 7, 30 or 90 days, then deleted. Event records
          without images are kept while your account is open.</p></div>
      <div class="card"><?php echo watchlog_icon('people',24); ?><h3>Account security</h3>
        <p>Only people you invite can see your tenant data, each with an owner, admin or read-only role.
          Traffic to WatchLog is over HTTPS.</p></div>
    </div>
    <div class="note-card" style="margin-top:28px">
      <strong>Plain about the boundary.</strong> WatchLog does not do facial recognition, does not provide
      live camera browsing through the portal, and makes no SOC&nbsp;2, ISO or similar certification claim it does not hold.
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Private by design. See it for yourself.</h2>
    <p class="lead measure">Keep the recorder private and add an outbound-only intelligence layer.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
