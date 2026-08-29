<?php if (!defined('ABSPATH')) { exit; } get_header(); ?>
<article class="page-body">
  <div class="wrap">
    <?php if (have_posts()) {
        while (have_posts()) { the_post(); ?>
          <h2><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h2>
          <?php the_excerpt();
        }
    } else { ?>
      <h1>Nothing here yet</h1>
      <p class="lede">There is no post at this address.
        <a href="<?php echo esc_url(home_url('/')); ?>">Back to the home page</a>.</p>
    <?php } ?>
  </div>
</article>
<?php get_footer(); ?>
