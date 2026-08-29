<?php
/**
 * WatchLog theme.
 *
 * Deliberately small. The marketing site is seven mostly-static pages;
 * a page builder would add weight, a plugin surface and an upgrade
 * treadmill for no benefit the customer can see.
 */

if (!defined('ABSPATH')) { exit; }

function watchlog_setup() {
    add_theme_support('title-tag');
    add_theme_support('post-thumbnails');
    add_theme_support('html5', ['search-form', 'comment-list', 'gallery', 'caption', 'style', 'script']);
    register_nav_menus([
        'primary' => 'Primary',
        'footer'  => 'Footer',
    ]);
}
add_action('after_setup_theme', 'watchlog_setup');

function watchlog_assets() {
    // Geist is the geometric grotesque the identity calls for; Inter is
    // the fallback that is already on most machines.
    wp_enqueue_style('watchlog-fonts',
        'https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap',
        [], null);
    wp_enqueue_style('watchlog', get_stylesheet_uri(), ['watchlog-fonts'],
        wp_get_theme()->get('Version'));
}
add_action('wp_enqueue_scripts', 'watchlog_assets');

/**
 * The monogram, inline.
 *
 * Inline rather than an <img> so it inherits colour from its context and
 * cannot flash in late on a cold cache. The path is generated from the
 * identity sheets by tools/build_brand_assets.py — do not hand-edit it.
 */
function watchlog_mark($height = 22) {
    $svg = get_template_directory() . '/mark.svg';
    if (!file_exists($svg)) { return ''; }
    return file_get_contents($svg);
}

/** Comments are off: this is a marketing site, not a blog. */
add_filter('comments_open', '__return_false', 20, 2);
add_filter('pings_open', '__return_false', 20, 2);

/** Trim WordPress's default head clutter we do not use. */
remove_action('wp_head', 'wp_generator');
remove_action('wp_head', 'wlwmanifest_link');
remove_action('wp_head', 'rsd_link');
