<?php
/**
 * Icon set.
 *
 * Authored here rather than assembled from the generated brand sheets on
 * purpose. A UI icon set only reads as a set if every icon shares one
 * grid, one stroke weight and one terminal style. Eight images generated
 * separately do not, however good each looks alone - they land at
 * different optical weights and the row looks broken.
 *
 * Rules, applied to all of them:
 *   24x24 viewBox, 1.75 stroke, round caps and joins, no fills.
 *   Stroke is currentColor, so an icon takes its colour from context and
 *   works on the light site and the dark bands without a second copy.
 *   Drawn on the pixel grid at 24px so they stay crisp at that size.
 *
 * Stroked rather than solid is the right call HERE and the opposite of
 * the monogram rule: these are never rendered below 20px, where stroke
 * survives; the monogram has to work at 16px, where it would not.
 */

if (!defined('ABSPATH')) { exit; }

function watchlog_icon($name, $size = 24, $class = '') {
    $p = [

    // --- the product -------------------------------------------------
    'camera' =>
        '<path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h2L9 4h6l1.5 2h2A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z"/>'.
        '<circle cx="12" cy="12" r="3.2"/>',

    'recorder' =>
        '<rect x="2.5" y="7" width="19" height="10" rx="2"/>'.
        '<path d="M6 11.5h1.5M10 11.5h8"/><circle cx="18" cy="14.5" r="1"/>',

    // A shield would be wrong: this product does not protect, it reports.
    'report' =>
        '<path d="M6 3.5h8.5L19 8v12.5H6z"/><path d="M14 3.5V8h5"/>'.
        '<path d="M9 12.5h7M9 16h4.5"/>',

    'clock' =>
        '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>',

    'moon' =>
        '<path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.4 8.4 0 1 0 20 14.2z"/>',

    'alert' =>
        '<path d="M12 4.5 21 19.5H3z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".6" fill="currentColor" stroke="none"/>',

    'check' =>
        '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.2l2.4 2.4 4.6-4.8"/>',

    // --- how it works -------------------------------------------------
    'pc' =>
        '<rect x="3" y="5" width="18" height="11" rx="1.8"/>'.
        '<path d="M8.5 20h7M12 16v4"/>',

    'cloud' =>
        '<path d="M7.5 18.5A4 4 0 0 1 7.2 10.6a5.2 5.2 0 0 1 10-1.2 3.9 3.9 0 0 1-.7 9.1z"/>',

    'arrow-out' =>
        '<path d="M4 12h13"/><path d="M12.5 7.5 17.5 12l-5 4.5"/>',

    'shield-off' =>   // "not a guard service"
        '<path d="M12 3.5 19.5 6v6c0 4.2-3 7.4-7.5 8.5C7.5 19.4 4.5 16.2 4.5 12V6z"/>'.
        '<path d="M4 4l16 16"/>',

    // --- portal / features ---------------------------------------------
    'chart' =>
        '<path d="M4 19.5V4.5"/><path d="M4 19.5h16"/>'.
        '<path d="M8 16.5v-4M12.5 16.5V8M17 16.5v-6"/>',

    'sites' =>
        '<path d="M12 21s6.5-5.4 6.5-10.2A6.5 6.5 0 0 0 5.5 10.8C5.5 15.6 12 21 12 21z"/>'.
        '<circle cx="12" cy="10.6" r="2.4"/>',

    'people' =>
        '<circle cx="9" cy="8.5" r="3.2"/>'.
        '<path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/>'.
        '<path d="M16 5.6a3.2 3.2 0 0 1 0 5.8M17.5 14.6a5.5 5.5 0 0 1 3 4.9"/>',

    'lock' =>
        '<rect x="5" y="10.5" width="14" height="9.5" rx="2"/>'.
        '<path d="M8.5 10.5V7.8a3.5 3.5 0 0 1 7 0v2.7"/>',

    'menu' =>
        '<path d="M4 7h16M4 12h16M4 17h16"/>',

    'whatsapp' =>
        '<path d="M3.8 20.2l1.2-4.2A8 8 0 1 1 8.2 19z"/>'.
        '<path d="M9.2 9.4c-.3 1.6 2 4.9 4 5.3.7.1 1.6-.5 1.8-1.2l-1.6-.9-.9.9c-1-.4-2-1.4-2.4-2.4l.9-.9-.9-1.6c-.7.2-.8.5-.9.8z"/>',

    // --- navigation / ui ----------------------------------------------
    'arrow-right' => '<path d="M4.5 12h14"/><path d="M13 6l6 6-6 6"/>',
    'chevron'     => '<path d="M6 9.5l6 6 6-6"/>',
    'close'       => '<path d="M6 6l12 12M18 6L6 18"/>',
    'layers'      => '<path d="M12 3.5 21 8l-9 4.5L3 8z"/><path d="M3 12l9 4.5L21 12"/><path d="M3 16l9 4.5L21 16"/>',
    'mail'        => '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M4 7l8 6 8-6"/>',
    'shield'      => '<path d="M12 3.2 19.5 6v6c0 4.3-3.1 7.6-7.5 8.8C7.6 19.6 4.5 16.3 4.5 12V6z"/><path d="M8.7 12.2l2.2 2.2 4.4-4.6"/>',

    // --- solutions -----------------------------------------------------
    'box'      => '<path d="M12 3.5 20 7v10l-8 3.5L4 17V7z"/><path d="M4 7l8 3.5L20 7M12 10.5V20"/>',
    'store'    => '<path d="M4.5 10.5V19h15v-8.5"/><path d="M3.5 10.5 5 5h14l1.5 5.5a2.6 2.6 0 0 1-5 .3 2.6 2.6 0 0 1-5 0 2.6 2.6 0 0 1-5-.3z"/><path d="M10 19v-4h4v4"/>',
    'factory'  => '<path d="M3.5 20.5V10l5 3.5V10l5 3.5V10l5 3.5v7z"/><path d="M3.5 20.5h17"/><path d="M7 17h.01M11 17h.01M15 17h.01"/>',
    'school'   => '<path d="M12 4 2.5 8.5 12 13l9.5-4.5z"/><path d="M6.5 10.8V15c0 1.3 2.5 2.5 5.5 2.5s5.5-1.2 5.5-2.5v-4.2"/><path d="M21.5 8.5v5"/>',
    'building' => '<rect x="5.5" y="3.5" width="13" height="17" rx="1.5"/><path d="M9 7.5h2M13 7.5h2M9 11h2M13 11h2M9 14.5h2M13 14.5h2M10 20.5v-3h4v3"/>',
    ];

    if (!isset($p[$name])) { return ''; }
    $cls = trim('ico ' . $class);
    return sprintf(
        '<svg class="%s" width="%d" height="%d" viewBox="0 0 24 24" fill="none" '.
        'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" '.
        'stroke-linejoin="round" aria-hidden="true">%s</svg>',
        esc_attr($cls), (int) $size, (int) $size, $p[$name]);
}
