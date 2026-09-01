<?php
/* Setup: what you need + how to install. Conversion page. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap-wide page-hero-split wide-media">
    <div>
      <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Setup</span></nav>
      <span class="eyebrow">Setup</span>
      <h1>What you'll need, before you sign up.</h1>
      <p class="lead">Stated here rather than discovered during onboarding. If you have these four things,
        you're about ten minutes from your first report.</p>
      <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
        <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="page-hero-media"><?php echo watchlog_pic('environment-site-pc','A site PC beside the recorder, ready to run the WatchLog Agent',1800,1200,'frame-plain','(max-width:900px) 92vw, 52vw'); ?></div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">The four things</span><h2>What you need at the site.</h2></div>
    <div class="grid g2">
      <div class="card"><?php echo watchlog_icon('recorder',24); ?><h3>A supported recorder</h3><p>Hikvision or Dahua are validated; HiLook, Imou, CP&nbsp;Plus, Uniview, Tiandy and most ONVIF units are protocol-compatible. Not sure? <a href="<?php echo watchlog_url('contact'); ?>">Send us the label.</a></p></div>
      <div class="card"><?php echo watchlog_icon('pc',24); ?><h3>A Windows PC at the site</h3><p>On the same network as the recorder, that stays switched on. Any ordinary office machine will do. It runs quietly in the background.</p></div>
      <div class="card"><?php echo watchlog_icon('lock',24); ?><h3>The recorder's login</h3><p>Its admin username and password. These are typed into the Agent at the site and never leave that PC.</p></div>
      <div class="card"><?php echo watchlog_icon('cloud',24); ?><h3>An ordinary internet connection</h3><p>No fixed IP, no port forwarding, no firewall changes. The Agent connects outward only.</p></div>
    </div>
    <div class="note-card" style="margin-top:24px"><strong>Not supported:</strong> the cheapest unbranded
      recorders (typically Xiongmai or Hisilicon boards sold without a brand name) don't speak a standard
      protocol reliably enough. If you're unsure what you have, send a photo of the label first.</div>
  </div>
</section>

<section class="cloud">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Installing</span><h2>Step by step, about ten minutes.</h2></div>
    <div class="steps">
      <div class="step"><div><h3>Create an account and add your site</h3><p>Start the free trial and add your first site. You'll be given an enrollment code.</p></div></div>
      <div class="step"><div><h3>Download the WatchLog Agent</h3><p>Onto the Windows PC at the site.</p></div></div>
      <div class="step"><div><h3>Run it and point it at the recorder</h3><p>A setup wizard asks for the recorder's address and login, then searches the network if you don't know the address. If it can't reach the recorder, it tells you why in plain language.</p></div></div>
      <div class="step"><div><h3>Paste the enrollment code</h3><p>The site appears in your portal within a minute, and its cameras sync automatically.</p></div></div>
      <div class="step"><div><h3>Choose who gets the daily report</h3><p>Add recipients and pick a channel: WhatsApp, email, or both.</p></div></div>
    </div>
    <div class="feature" style="margin-top:clamp(40px,5vw,64px)">
      <div class="f-media"><?php echo watchlog_shot('product-setup','The WatchLog onboarding stepper: enrollment, recorder, cameras, ready',1500,1000,'(max-width:900px) 92vw, 48vw',true); ?></div>
      <div class="f-copy">
        <span class="f-kicker"><?php echo watchlog_icon('check',20); ?> You'll see it come up live</span>
        <h3>The portal fills in as it connects.</h3>
        <p>Enrollment, then the recorder, then the cameras, then ready. Each step shows in the portal so
          you can watch the site come online rather than wonder whether it worked.</p>
      </div>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Have the four things? Start now.</h2>
    <p class="lead measure">Fourteen days free, no card. You'll know it works within minutes.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
