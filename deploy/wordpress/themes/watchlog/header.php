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
    <button class="nav-toggle" aria-label="Menu" aria-expanded="false"
            aria-controls="site-nav" onclick="var n=document.getElementById('site-nav');var o=this.getAttribute('aria-expanded')==='true';this.setAttribute('aria-expanded',!o);n.classList.toggle('open')">
      <?php echo watchlog_icon('menu', 24); ?>
    </button>
    <nav class="nav" id="site-nav">
      <?php
      if (has_nav_menu('primary')) {
          wp_nav_menu(['theme_location' => 'primary', 'container' => false,
                       'items_wrap' => '%3$s', 'depth' => 1]);
      }
      ?>
      <a class="nav-signin" href="<?php echo esc_url(watchlog_login_url()); ?>">Sign in</a>
      <a class="btn btn-primary" href="<?php echo esc_url(watchlog_signup_url()); ?>">Start free</a>
    </nav>
  </div>
</header>
<main>
