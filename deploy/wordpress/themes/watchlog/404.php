<?php if (!defined('ABSPATH')) { exit; } get_header(); ?>
<article class="page-body">
  <div class="wrap">
    <h1>That page does not exist</h1>
    <p class="lede">The address may have changed.
      <a href="<?php echo esc_url(home_url('/')); ?>">Go to the home page</a>.</p>
  </div>
</article>
<?php get_footer(); ?>
