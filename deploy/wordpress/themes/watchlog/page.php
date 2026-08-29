<?php if (!defined('ABSPATH')) { exit; } get_header(); ?>
<article class="page-body">
  <div class="wrap">
    <h1><?php the_title(); ?></h1>
    <?php while (have_posts()) { the_post(); the_content(); } ?>
  </div>
</article>
<?php get_footer(); ?>
