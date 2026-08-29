<?php if (!defined('ABSPATH')) { exit; } ?>
</main>
<footer class="site-footer">
  <div class="wrap">
    <div class="foot-grid">
      <div>
        <a class="brand" style="color:#fff" href="<?php echo esc_url(home_url('/')); ?>">
          <?php echo watchlog_mark(); ?><span>WatchLog</span>
        </a>
        <p style="margin-top:12px;max-width:34ch">
          Your cameras already see everything. WatchLog tells you what they saw.
        </p>
      </div>
      <div class="foot-links">
        <?php
        if (has_nav_menu('footer')) {
            wp_nav_menu(['theme_location' => 'footer', 'container' => false,
                         'items_wrap' => '%3$s', 'depth' => 1]);
        }
        ?>
      </div>
    </div>
    <div class="foot-note">
      &copy; <?php echo esc_html(date('Y')); ?> WatchLog. Built by Vision Infinity.
      <br>WatchLog observes and reports. It does not prevent incidents, and it is
      not a guarding service.
    </div>
  </div>
</footer>
<?php wp_footer(); ?>
</body>
</html>
