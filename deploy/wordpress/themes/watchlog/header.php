<?php if (!defined('ABSPATH')) { exit; } ?>
<!doctype html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="<?php bloginfo('charset'); ?>">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#101017">
<?php wp_head(); ?>
</head>
<body <?php body_class(); ?>>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="<?php echo esc_url(home_url('/')); ?>">
      <?php echo watchlog_mark(); ?><span>WatchLog</span>
    </a>
    <nav class="nav">
      <?php
      if (has_nav_menu('primary')) {
          wp_nav_menu(['theme_location' => 'primary', 'container' => false,
                       'items_wrap' => '%3$s', 'depth' => 1]);
      }
      ?>
      <a class="btn btn-primary" href="<?php echo esc_url(get_option('watchlog_portal_url', '#')); ?>">Sign in</a>
    </nav>
  </div>
</header>
<main>
