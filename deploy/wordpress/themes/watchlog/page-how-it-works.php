<?php
/* How it works: technical but accessible; should satisfy an IT/security reviewer. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>How it works</span></nav>
    <span class="eyebrow">How it works</span>
    <h1>Your recorder stays private.</h1>
    <p class="lead measure">A small program on a PC you already have reads the events your recorder
      already logs, filters them on site, and sends only what matters, outward, never inward.</p>
  </div>
  <div class="wrap-wide" style="margin-top:clamp(32px,4vw,52px)">
    <?php echo watchlog_pic('diagram-architecture','Recorder to Windows Site Agent to secure outbound connection to WatchLog cloud to portal and reports',1800,1000,'',' (max-width:1100px) 92vw, 1100px'); ?>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Step by step</span><h2>From recorder to report.</h2></div>
    <div class="steps">
      <div class="step"><div><h3>Your existing recorder</h3><p>Keep the cameras and recorder you already own. Recorders already detect motion and log events. WatchLog reads that log; it does not replace anything.</p></div></div>
      <div class="step"><div><h3>The Windows Site Agent</h3><p>A lightweight program installs on any always-on Windows PC on the same network as the recorder. No window, minimal resources. It authenticates to the recorder locally with credentials that never leave that PC.</p></div></div>
      <div class="step"><div><h3>A still, and on-site AI</h3><p>For each event it captures one still from the camera and checks it (on your own machine) for a person, car or motorcycle. Rain, headlights and the IR lamp are dropped and never leave the building. If the detector can't run, the event is kept rather than silently dropped.</p></div></div>
      <div class="step"><div><h3>Outbound sync only</h3><p>Only the events that passed the filter are sent out to WatchLog, each with its still. The connection goes one way: no port forwarding, no inbound access, no public RTSP.</p></div></div>
      <div class="step"><div><h3>The portal</h3><p>Your sites, incidents and camera health appear in the portal within a minute of enrollment, scoped to your account and no one else's.</p></div></div>
      <div class="step"><div><h3>The daily report</h3><p>Every morning the right people get a summary (counts by camera and type, after-hours activity, and anything that went quiet) on WhatsApp, email, or both, in the site's local time.</p></div></div>
      <div class="step"><div><h3>If the internet drops</h3><p>Events are buffered on the site PC and sent when the connection returns. A bad line delays a report; it doesn't lose events.</p></div></div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap split">
    <div>
      <span class="eyebrow">On the ground</span>
      <h2>What runs where.</h2>
      <p>The recorder and the site PC do the sensitive work locally. WatchLog only ever holds the
        minimum needed to tell you what happened.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('recorder',20); ?> Recorder: your video, on your premises</li>
        <li><?php echo watchlog_icon('pc',20); ?> Site PC: the Agent, the credentials, the on-site filtering</li>
        <li><?php echo watchlog_icon('cloud',20); ?> WatchLog: event records, one still per incident, health signals</li>
      </ul>
      <a class="arrow-link" href="<?php echo watchlog_url('security'); ?>">Read the security model <?php echo watchlog_icon('arrow-right',18); ?></a>
    </div>
    <div class="grid" style="gap:16px">
      <?php echo watchlog_pic('environment-recorder','A CCTV recorder installed at a site',1800,1200,'frame-plain','(max-width:900px) 92vw, 46vw'); ?>
      <?php echo watchlog_pic('environment-site-pc','The site PC that runs the WatchLog Agent',1800,1200,'frame-plain','(max-width:900px) 92vw, 46vw'); ?>
    </div>
  </div>
</section>

<section class="dark field ai">
  <div class="wrap-wide">
    <div class="sec-head center"><span class="eyebrow">On-site filtering</span>
      <h2>Noise is removed before it becomes an incident.</h2></div>
    <div style="max-width:1100px;margin:0 auto"><?php echo watchlog_pic('diagram-ai-filtering','Raw event to on-site AI to a validated incident that is kept',1800,1000,'',' (max-width:1100px) 92vw, 1100px'); ?></div>
    <p class="center note-line">Detects person, car and motorcycle. Not facial recognition.</p>
  </div>
</section>

<section class="light cta-band">
  <div class="wrap center">
    <h2 style="color:var(--ink-900)">Ten minutes to your first report.</h2>
    <p class="lead measure" style="margin-inline:auto">Most of it is finding the recorder's password.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-secondary btn-lg" href="<?php echo watchlog_url('setup'); ?>">What you'll need</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
