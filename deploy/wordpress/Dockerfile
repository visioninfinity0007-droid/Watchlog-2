# WatchLog marketing site image.
#
# The stock WordPress image plus our theme. Built from git so the site's
# design is version-controlled and reviewable, rather than uploaded
# through wp-admin and existing only inside a volume nobody can diff.
FROM wordpress:6-php8.3-apache

COPY themes/watchlog /usr/src/wordpress/wp-content/themes/watchlog

# The site's pages, menus and settings, so content is reproducible too:
#   docker exec <container> bash /usr/src/wordpress/site-content.sh
COPY site-content.sh /usr/src/wordpress/site-content.sh

# WP-CLI, used by that script and for routine admin. Fetched at build time
# so the running container never needs outbound access.
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/wp \
      https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar; \
    chmod +x /usr/local/bin/wp; \
    wp --allow-root --version

# See entrypoint.sh: the stock entrypoint only seeds /var/www/html when it
# is missing, so once the volume exists an updated theme baked into the
# image is silently ignored - and a design change would never actually
# ship. This wrapper syncs the theme on every start.
COPY entrypoint.sh /usr/local/bin/watchlog-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/watchlog-entrypoint.sh \
                     /usr/src/wordpress/site-content.sh; \
    chmod +x /usr/local/bin/watchlog-entrypoint.sh

ENTRYPOINT ["watchlog-entrypoint.sh"]
CMD ["apache2-foreground"]
