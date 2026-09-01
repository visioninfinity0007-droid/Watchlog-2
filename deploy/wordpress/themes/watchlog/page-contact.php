<?php
/* Contact — every path a REAL action. No invented contact data. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
$login  = esc_url(watchlog_login_url());
$wa_label = watchlog_whatsapp_url('Hi WatchLog — I would like to check my recorder.');
$mail_label = watchlog_mailto('WatchLog — recorder compatibility');

/* Best available real action for a path: configured WhatsApp, then email,
   then a sensible product action. Never fabricates a destination. */
function wl_action($btnclass, $wa_text, $mail_subject, $fallback_url, $fallback_label) {
  $wa = watchlog_whatsapp_url($wa_text);
  $mail = watchlog_mailto($mail_subject);
  if ($wa)   { return sprintf('<a class="btn %s" href="%s">Message on WhatsApp</a>', $btnclass, esc_url($wa)); }
  if ($mail) { return sprintf('<a class="btn %s" href="%s">Email us</a>', $btnclass, esc_url($mail)); }
  return sprintf('<a class="btn %s" href="%s">%s</a>', $btnclass, esc_url($fallback_url), esc_html($fallback_label));
}
?>
<section class="page-hero dark field">
  <div class="wrap" style="max-width:52rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Contact</span></nav>
    <span class="eyebrow">Contact</span>
    <h1>Let's check whether WatchLog fits your site.</h1>
    <p class="lead measure">The fastest answer to "will it work with my recorder?" is a photo of the
      label, or the free trial — you'll know within minutes of installing.</p>
    <div class="cta-row">
      <?php echo wl_action('btn-primary btn-lg', 'Hi WatchLog — I would like to check my recorder.', 'WatchLog — recorder compatibility', watchlog_url('setup'), 'Check my recorder'); ?>
      <a class="btn btn-ghost btn-lg" href="<?php echo $signup; ?>">Start free</a>
    </div>
  </div>
</section>

<section class="light">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">How can we help?</span><h2>Pick the path that fits.</h2></div>
    <div class="paths">
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('recorder',22); ?></span>
        <h3>Recorder compatibility</h3>
        <p>Send a photo of the label on the front or back of your recorder and we'll tell you yes or no,
          usually the same day — before you spend anything.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog — here is my recorder label, is it compatible?', 'WatchLog — recorder compatibility', $signup, 'Start free & test it'); ?>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('sites',22); ?></span>
        <h3>Multiple sites</h3>
        <p>Rolling out across several locations? We'll help you plan recipients, roles and per-site
          reporting, and talk through Enterprise.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog — we have multiple sites and would like to discuss a rollout.', 'WatchLog — multi-site rollout', watchlog_url('pricing'), 'See pricing'); ?>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('people',22); ?></span>
        <h3>Existing customer</h3>
        <p>A site stopped reporting, or you need a hand? Tell us which site and roughly when — the portal
          shows the last time each site was heard from, on the Site Health page.</p>
        <a class="btn btn-secondary" href="<?php echo $login; ?>">Sign in to the portal</a>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('mail',22); ?></span>
        <h3>General enquiry</h3>
        <p>Anything else — partnerships, questions, or a straight answer about what WatchLog does and
          doesn't do.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog — I have a question.', 'WatchLog — enquiry', watchlog_url('how-it-works'), 'How it works'); ?>
      </div>
    </div>
    <?php if (!watchlog_whatsapp_url() && !watchlog_mailto()) : ?>
    <p class="center" style="margin-top:26px;color:var(--slate-500)">The quickest way to see WatchLog on your
      own recorder is the free 14-day trial — it needs no card, and the setup wizard tells you within minutes
      whether your recorder is supported.</p>
    <?php endif; ?>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Or just try it on your own recorder.</h2>
    <p class="lead measure">Fourteen days free, no card. The install is the real compatibility test.</p>
    <div class="cta-row" style="justify-content:center">
      <a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a>
      <a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('compatibility'); ?>">Compatibility</a>
    </div>
  </div>
</section>
<?php get_footer(); ?>
