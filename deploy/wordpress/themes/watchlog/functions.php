<?php
/**
 * WatchLog theme.
 *
 * Deliberately small. The marketing site is seven mostly-static pages;
 * a page builder would add weight, a plugin surface and an upgrade
 * treadmill for no benefit the customer can see.
 */

if (!defined('ABSPATH')) { exit; }

require_once get_template_directory() . '/icons.php';

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

    // Inner-page furniture: header band, prose, tables, TOC.
    if (!is_front_page()) {
        wp_enqueue_style('watchlog-pages',
            get_template_directory_uri() . '/pages.css', ['watchlog'],
            wp_get_theme()->get('Version'));
    }

    // Home page composition, loaded only where it is used.
    if (is_front_page()) {
        wp_enqueue_style('watchlog-home',
            get_template_directory_uri() . '/home.css', ['watchlog'],
            wp_get_theme()->get('Version'));
    }
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

/**
 * A theme image, or nothing.
 *
 * Every photograph on this site is optional. The layouts were built to
 * stand up without them - segment tiles fall back to an icon, the hero
 * to a gradient - so a missing or not-yet-supplied file degrades the
 * page rather than breaking it. That also means the site can ship before
 * the photography exists, which is what happened.
 */
function watchlog_has_img($name) {
    return file_exists(get_template_directory() . "/img/$name.jpg");
}

function watchlog_img_url($name) {
    return get_template_directory_uri() . "/img/$name.jpg";
}

/**
 * `alt` is required, never decorative-by-accident. Pass '' deliberately
 * for images that repeat adjacent text - a screen reader announcing a
 * filename is worse than silence.
 */
function watchlog_img($name, $alt, $w, $h, $class = '') {
    if (!watchlog_has_img($name)) { return ''; }
    return sprintf(
        '<img src="%s" alt="%s" width="%d" height="%d" class="%s" '
        . 'loading="lazy" decoding="async">',
        esc_url(watchlog_img_url($name)), esc_attr($alt),
        $w, $h, esc_attr($class));
}
