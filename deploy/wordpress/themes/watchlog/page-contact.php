<?php
/* Contact: real actions only. Business identity is configuration-driven and never invented. */
if (!defined('ABSPATH')) { exit; }
get_header();
$signup = esc_url(watchlog_signup_url());
$login  = esc_url(watchlog_login_url());
$legal_name = trim(get_option('watchlog_legal_name', getenv('WATCHLOG_LEGAL_NAME') ?: ''));
$business_address = trim(get_option('watchlog_business_address', getenv('WATCHLOG_BUSINESS_ADDRESS') ?: ''));
$contact_phone = trim(get_option('watchlog_contact_phone', getenv('WATCHLOG_CONTACT_PHONE_DISPLAY') ?: ''));
$billing_email = trim(get_option('watchlog_billing_email', getenv('WATCHLOG_BILLING_EMAIL') ?: watchlog_contact_email()));
$merchant_complete = ($legal_name !== '' && $business_address !== '' && $contact_phone !== '' && $billing_email !== '');

function wl_action($btnclass, $wa_text, $mail_subject, $fallback_url, $fallback_label) {
  $wa = watchlog_whatsapp_url($wa_text);
  $mail = watchlog_mailto($mail_subject);
  if ($wa)   { return sprintf('<a class="btn %s" href="%s">Message on WhatsApp</a>', $btnclass, esc_url($wa)); }
  if ($mail) { return sprintf('<a class="btn %s" href="%s">Email us</a>', $btnclass, esc_url($mail)); }
  return sprintf('<a class="btn %s" href="%s">%s</a>', $btnclass, esc_url($fallback_url), esc_html($fallback_label));
}
?>
<section class="page-hero dark field glow-field grid-bg">
  <div class="wrap" style="max-width:54rem">
    <nav class="crumbs" aria-label="Breadcrumb"><a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span class="sep">/</span><span>Contact</span></nav>
    <span class="eyebrow">Contact</span>
    <h1>Tell us the site, the camera system and the outcome you need.</h1>
    <p class="lead measure">Use WatchLog for recorder compatibility, rollout planning, current-customer support, after-sales help, partnerships or a custom analytics and integration discussion.</p>
    <div class="cta-row">
      <?php echo wl_action('btn-primary btn-lg', 'Hi WatchLog, I would like to discuss my site and camera analytics requirements.', 'WatchLog: site and analytics enquiry', watchlog_url('platform'), 'See the platform'); ?>
      <a class="btn btn-ghost btn-lg" href="<?php echo $signup; ?>">Start free</a>
    </div>
  </div>
</section>

<section class="surface">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">How can we help?</span><h2>Pick the path that fits.</h2></div>
    <div class="paths">
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('recorder',22); ?></span>
        <h3>Recorder compatibility</h3>
        <p>Share the recorder model and site context so we can assess the environment. Final compatibility is confirmed against the actual recorder and camera views.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog, I would like to check my recorder compatibility.', 'WatchLog: recorder compatibility', $signup, 'Start free and test it'); ?>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('sites',22); ?></span>
        <h3>Multi-site rollout or QSR pilot</h3>
        <p>Planning several branches or a Control Room pilot? We can discuss site structure, reporting, camera-layout acceptance and what belongs in the current product versus the pilot.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog, I would like to discuss a multi-site or QSR pilot.', 'WatchLog: multi-site or QSR pilot', watchlog_url('solutions/quick-service-restaurants'), 'See QSR solutions'); ?>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('people',22); ?></span>
        <h3>Customer support &amp; after-sales</h3>
        <p>If a site stopped reporting, a camera view needs recalibration or you want to expand a working deployment, tell us which site and what changed.</p>
        <a class="btn btn-secondary" href="<?php echo $login; ?>">Sign in to the portal</a>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('box',22); ?></span>
        <h3>Custom integrations</h3>
        <p>POS, Shopify, attendance machines, CRM and other operational systems can be scoped as Custom Solution work after the data flow and acceptance criteria are defined.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog, I would like to discuss a custom integration.', 'WatchLog: custom integration', watchlog_url('integrations'), 'See integrations'); ?>
      </div>
      <div class="path-card">
        <span class="p-ic"><?php echo watchlog_icon('mail',22); ?></span>
        <h3>Partnerships &amp; general enquiries</h3>
        <p>Technology partnerships, camera ecosystem discussions and other business enquiries can start here. We only describe a vendor as a partner after the relationship is formally approved.</p>
        <?php echo wl_action('btn-secondary', 'Hi WatchLog, I have a partnership or general enquiry.', 'WatchLog: partnership or general enquiry', watchlog_url('how-it-works'), 'How it works'); ?>
      </div>
    </div>
  </div>
</section>

<section class="surface-cool">
  <div class="wrap">
    <div class="sec-head"><span class="eyebrow">Business details</span><h2>Merchant-facing contact information.</h2></div>
    <?php if ($merchant_complete) : ?>
      <div class="note-card">
        <p><strong>Legal business name:</strong> <?php echo esc_html($legal_name); ?><br>
        <strong>Business address:</strong> <?php echo esc_html($business_address); ?><br>
        <strong>Local phone:</strong> <?php echo esc_html($contact_phone); ?><br>
        <strong>Billing / support email:</strong> <?php echo esc_html($billing_email); ?></p>
      </div>
    <?php else : ?>
      <div class="note-card">
        <p><strong>Merchant onboarding notice:</strong> the exact legal business name, physical business address, Pakistani local phone number and official billing/support email still require owner-supplied values. The website is ready to publish them from deployment configuration as soon as they are provided.</p>
      </div>
    <?php endif; ?>
    <p style="margin-top:22px">Commercial policy: <a href="<?php echo watchlog_url('refund-cancellation-service-delivery'); ?>">Refund, Cancellation &amp; Service Delivery</a> · <a href="<?php echo watchlog_url('terms'); ?>">Terms</a> · <a href="<?php echo watchlog_url('privacy'); ?>">Privacy</a></p>
  </div>
</section>

<section class="dark field cta-band">
  <div class="wrap center">
    <h2>Start with the real site.</h2>
    <p class="lead measure">The recorder, camera view and operational question determine what can be measured reliably.</p>
    <div class="cta-row" style="justify-content:center"><a class="btn btn-primary btn-lg" href="<?php echo $signup; ?>">Start free</a><a class="btn btn-ghost btn-lg" href="<?php echo watchlog_url('faq'); ?>">Read the FAQ</a></div>
  </div>
</section>
<?php get_footer(); ?>
