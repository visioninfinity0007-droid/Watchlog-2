<?php
/* Compatibility: precise validation language (see PUBLIC_CLAIMS_MATRIX). */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:56rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Compatibility</span></nav>
    <span class="eyebrow">Compatibility</span>
    <h1>Keep the cameras. Keep the recorder. Add WatchLog.</h1>
    <p class="lead measure">WatchLog works with the recorder you already own. There is no new hardware to buy
      and nothing to rip out, just a small program on a PC at the site.</p>
    <div class="cta-row"><a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></div>
  </div>
  <div class="wrap-wide" style="margin-top:clamp(32px,4vw,52px)">
    <?php echo watchlog_flow([
      ['camera','Your cameras','Keep them'],
      ['recorder','Your recorder','Keep it'],
      ['box','WatchLog Agent','Add it',true],
      ['report','Daily intelligence','Every morning'],
    ], ['aria'=>'Keep your existing cameras and recorder and add WatchLog']); ?>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Where your recorder fits</span>
      <h2>Three honest categories.</h2>
      <p class="lead measure">We would rather tell you before you spend anything than after. Compatibility
        depends on the exact model and firmware, so when in doubt we confirm your specific unit.</p></div>
    <div class="compat">
      <div class="compat-card ok">
        <h3><?php echo watchlog_icon('check',22); ?> Validated <span class="compat-badge badge-ok">Driver proven</span></h3>
        <p>Our drivers for these are exercised against the manufacturers' protocols, and Dahua has been
          seen on real hardware. Broad field validation across sites is ongoing.</p>
        <div class="chip-row">
          <span class="hw-chip">Hikvision (ISAPI)</span>
          <span class="hw-chip">Dahua (CGI)</span>
        </div>
      </div>
      <div class="compat-card maybe">
        <h3><?php echo watchlog_icon('recorder',22); ?> Protocol-compatible, but confirm your unit <span class="compat-badge badge-maybe">Check first</span></h3>
        <p>These usually speak a protocol WatchLog supports, but coverage varies by model and firmware.
          Send us your recorder's label and we'll confirm before you commit.</p>
        <div class="chip-row">
          <span class="hw-chip">HiLook</span><span class="hw-chip">Imou</span><span class="hw-chip">CP Plus</span>
          <span class="hw-chip">Uniview</span><span class="hw-chip">Tiandy</span><span class="hw-chip">Most ONVIF recorders</span>
        </div>
      </div>
      <div class="compat-card no">
        <h3><?php echo watchlog_icon('shield-off',22); ?> Not supported <span class="compat-badge badge-no">Unknown protocol</span></h3>
        <p>The cheapest unbranded recorders, typically built on Xiongmai or Hisilicon boards and sold
          without a brand name, don't speak a standard protocol reliably enough for us to support them.</p>
      </div>
    </div>
  </div>
</section>

<section class="cloud">
  <div class="wrap split">
    <div>
      <span class="eyebrow">The fastest check</span>
      <h2>Send us the label.</h2>
      <p>Not sure what you have? The label on the front or back of your recorder is usually enough for
        us to tell you yes or no the same day, before you sign up or spend anything.</p>
      <ul class="ticks">
        <li><?php echo watchlog_icon('camera',20); ?> Photograph the recorder's label</li>
        <li><?php echo watchlog_icon('report',20); ?> Send it to us with your rough camera count</li>
        <li><?php echo watchlog_icon('check',20); ?> We confirm compatibility, usually same day</li>
      </ul>
      <div class="cta-row"><a class="btn btn-primary" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a></div>
    </div>
    <div class="note-card">
      <h3 style="margin-top:0">A word on the ten-minute test</h3>
      <p style="margin-bottom:0">Even once a recorder is on the supported list, the real proof is the
        install itself. The setup wizard reaches your recorder and syncs its cameras in about ten
        minutes. If it can't, it tells you why in plain language. You'll know it works long before a
        trial ends, and the trial needs no card.</p>
    </div>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Most likely, yes. Let's confirm your unit.</h2>
    <p class="lead measure">Keep your cameras and recorder. Add WatchLog.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo watchlog_url('contact'); ?>">Check my recorder</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo $signup; ?>">Start free</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
