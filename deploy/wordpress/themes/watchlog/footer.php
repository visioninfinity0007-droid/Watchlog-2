<?php
if (!defined('ABSPATH')) { exit; }
$legal_name = trim(get_option('watchlog_legal_name', getenv('WATCHLOG_LEGAL_NAME') ?: ''));
$business_address = trim(get_option('watchlog_business_address', getenv('WATCHLOG_BUSINESS_ADDRESS') ?: ''));
$contact_phone = trim(get_option('watchlog_contact_phone', getenv('WATCHLOG_CONTACT_PHONE_DISPLAY') ?: ''));
$billing_email = trim(get_option('watchlog_billing_email', getenv('WATCHLOG_BILLING_EMAIL') ?: watchlog_contact_email()));
$merchant_complete = ($legal_name !== '' && $business_address !== '' && $contact_phone !== '' && $billing_email !== '');
?>
</main>
<footer class="site-footer">
  <div class="wrap-wide">
    <div class="foot-top">
      <div class="foot-brand">
        <a class="brand" href="<?php echo esc_url(home_url('/')); ?>"><?php echo watchlog_mark(); ?><span>WatchLog</span></a>
        <p>Video Analytics &amp; CCTV Intelligence for businesses that want more value from the cameras they already own.</p>
        <?php if ($merchant_complete) : ?>
          <p><strong><?php echo esc_html($legal_name); ?></strong><br><?php echo esc_html($business_address); ?><br><?php echo esc_html($contact_phone); ?><br><?php echo esc_html($billing_email); ?></p>
        <?php else : ?>
          <p><strong>Merchant onboarding notice:</strong> legal entity, physical address, local phone and billing email are awaiting final owner-supplied publication details.</p>
        <?php endif; ?>
      </div>
      <div class="foot-col">
        <h4>Product</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('platform'); ?>">Platform</a></li>
          <li><a href="<?php echo watchlog_url('incidents'); ?>">Incidents</a></li>
          <li><a href="<?php echo watchlog_url('reporting'); ?>">Reporting</a></li>
          <li><a href="<?php echo watchlog_url('site-health'); ?>">Site Health</a></li>
          <li><a href="<?php echo watchlog_url('integrations'); ?>">Integrations</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Solutions</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('solutions/quick-service-restaurants'); ?>">Quick-Service Restaurants</a></li>
          <li><a href="<?php echo watchlog_url('solutions/warehouses-logistics'); ?>">Warehouses &amp; Logistics</a></li>
          <li><a href="<?php echo watchlog_url('solutions/retail'); ?>">Retail</a></li>
          <li><a href="<?php echo watchlog_url('solutions/manufacturing'); ?>">Manufacturing &amp; Textiles</a></li>
          <li><a href="<?php echo watchlog_url('solutions/fuel-forecourt'); ?>">Fuel &amp; Forecourt</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Learn</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></li>
          <li><a href="<?php echo watchlog_url('compatibility'); ?>">Compatibility</a></li>
          <li><a href="<?php echo watchlog_url('security'); ?>">Security</a></li>
          <li><a href="<?php echo watchlog_url('pricing'); ?>">Pricing</a></li>
          <li><a href="<?php echo watchlog_url('faq'); ?>">FAQ</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Company &amp; policies</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('contact'); ?>">Contact</a></li>
          <li><a href="<?php echo watchlog_url('privacy'); ?>">Privacy</a></li>
          <li><a href="<?php echo watchlog_url('terms'); ?>">Terms</a></li>
          <li><a href="<?php echo watchlog_url('refund-cancellation-service-delivery'); ?>">Refund, cancellation &amp; delivery</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Account</h4>
        <ul>
          <li><a href="<?php echo esc_url(watchlog_signup_url()); ?>">Start free</a></li>
          <li><a href="<?php echo esc_url(watchlog_login_url()); ?>">Sign in</a></li>
          <li><a href="<?php echo watchlog_url('setup'); ?>">Setup</a></li>
        </ul>
      </div>
    </div>
    <div class="foot-note">
      <p>&copy; <?php echo esc_html(date('Y')); ?> WatchLog. Works alongside your security and operations teams. WatchLog observes, measures and reports; it is not a guarding, live-monitoring or emergency-response service.</p>
    </div>
  </div>
</footer>
<?php wp_footer(); ?>
</body>
</html>
