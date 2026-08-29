<?php
/**
 * Contact.
 *
 * Three routes, each with the ONE thing to include so the first reply
 * can be useful. A contact page that just says "get in touch" produces a
 * message saying "does this work with my cameras?" and a two-day round
 * trip to find out which cameras.
 */
if (!defined('ABSPATH')) { exit; }
get_header();
?>

<section class="page-hero">
  <div class="wrap">
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="<?php echo esc_url(home_url('/')); ?>">Home</a><span>/</span>Contact
    </nav>
    <h1>Talk to us</h1>
    <p class="lede">WhatsApp is fastest. Include the detail below and the first
      reply will usually answer your question outright.</p>
  </div>
</section>

<section>
  <div class="wrap">
    <div class="contact-grid">
      <div class="contact-card">
        <span class="ico-badge"><?php echo watchlog_icon('recorder'); ?></span>
        <h3>Will it work with our recorder?</h3>
        <p>Send a photo of the label on the underside or back of the box. That
          is normally enough for a yes or no the same day.</p>
        <a href="/setup/">What we support &rarr;</a>
      </div>
      <div class="contact-card">
        <span class="ico-badge"><?php echo watchlog_icon('people'); ?></span>
        <h3>Several sites</h3>
        <p>Tell us how many locations and roughly how many cameras at each, and
          we will give you a number rather than a range.</p>
        <a href="/pricing/">Pricing &rarr;</a>
      </div>
      <div class="contact-card">
        <span class="ico-badge warn"><?php echo watchlog_icon('alert'); ?></span>
        <h3>A site has stopped reporting</h3>
        <p>Say which site and roughly when it went quiet. The portal shows the
          last time each site was heard from, under site health.</p>
        <a href="/features/">Site health &rarr;</a>
      </div>
    </div>

    <div class="note" style="margin-top:34px">
      <?php echo watchlog_icon('lock', 20); ?>
      <p><strong>Never send us your recorder's password.</strong> We do not need
        it and we do not want it — it is typed in at your site and stays there.
        Anyone asking you for it, including someone claiming to be us, is not
        us.</p>
    </div>
  </div>
</section>

<?php get_footer(); ?>
