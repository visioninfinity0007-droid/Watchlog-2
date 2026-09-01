<?php if (!defined('ABSPATH')) { exit; } ?>
</main>
<footer class="site-footer">
  <div class="wrap-wide">
    <div class="foot-top">
      <div class="foot-brand">
        <a class="brand" href="<?php echo esc_url(home_url('/')); ?>"><?php echo watchlog_mark(); ?><span>WatchLog</span></a>
        <p>The intelligence layer for the CCTV you already own. Keep your cameras and recorder, and add WatchLog.</p>
      </div>
      <div class="foot-col">
        <h4>Product</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('platform'); ?>">Platform</a></li>
          <li><a href="<?php echo watchlog_url('incidents'); ?>">Incidents</a></li>
          <li><a href="<?php echo watchlog_url('reporting'); ?>">Reporting</a></li>
          <li><a href="<?php echo watchlog_url('site-health'); ?>">Site Health</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Solutions</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('solutions/warehouses-logistics'); ?>">Warehouses</a></li>
          <li><a href="<?php echo watchlog_url('solutions/retail'); ?>">Retail</a></li>
          <li><a href="<?php echo watchlog_url('solutions/manufacturing'); ?>">Manufacturing</a></li>
          <li><a href="<?php echo watchlog_url('solutions/schools-campuses'); ?>">Schools</a></li>
          <li><a href="<?php echo watchlog_url('solutions/offices'); ?>">Offices</a></li>
        </ul>
      </div>
      <div class="foot-col">
        <h4>Learn</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('how-it-works'); ?>">How it works</a></li>
          <li><a href="<?php echo watchlog_url('compatibility'); ?>">Compatibility</a></li>
          <li><a href="<?php echo watchlog_url('security'); ?>">Security</a></li>
          <li><a href="<?php echo watchlog_url('pricing'); ?>">Pricing</a></li>
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
      <div class="foot-col">
        <h4>Company</h4>
        <ul>
          <li><a href="<?php echo watchlog_url('contact'); ?>">Contact</a></li>
          <li><a href="<?php echo watchlog_url('privacy'); ?>">Privacy</a></li>
          <li><a href="<?php echo watchlog_url('terms'); ?>">Terms</a></li>
        </ul>
      </div>
    </div>
    <div class="foot-note">
      <p>&copy; <?php echo esc_html(date('Y')); ?> WatchLog. Works alongside your security team.
         WatchLog observes and reports; it is not a guarding or monitoring service and does not dispatch a response.</p>
    </div>
  </div>
</footer>
<?php wp_footer(); ?>
</body>
</html>
