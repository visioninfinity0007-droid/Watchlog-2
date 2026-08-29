# WatchLog marketing site image.
#
# The stock WordPress image plus our theme. Built from git so the site's
# design is version-controlled and reviewable, rather than uploaded
# through wp-admin and existing only inside a volume nobody can diff.
#
# The theme is placed in /usr/src/wordpress, NOT /var/www/html. The
# official entrypoint copies /usr/src/wordpress into the html volume for
# files that are missing, so a new or changed theme lands on the next
# start without touching the customer's uploads or database.
FROM wordpress:6-php8.3-apache

COPY themes/watchlog /usr/src/wordpress/wp-content/themes/watchlog

# WP-CLI, used by the site-content script and for routine admin. Fetched
# at build time so the running container never needs outbound access.
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/wp \
      https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar; \
    chmod +x /usr/local/bin/wp; \
    wp --allow-root --version
