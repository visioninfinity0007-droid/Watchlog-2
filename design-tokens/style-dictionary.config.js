// WatchLog design tokens.
//
// One source, several consumers: the WordPress marketing theme takes the
// CSS/SCSS build, the Next.js portal takes the CSS/JS build. This file is
// the reason the two properties cannot drift apart - a colour changes in
// tokens/*.json or it does not change at all.
//
// Modelled on projects/content-engine/design-tokens, which established
// this pattern for Vision Infinity.

module.exports = {
  source: ["tokens/**/*.json"],
  platforms: {
    css: {
      transformGroup: "css",
      buildPath: "build/css/",
      files: [{
        destination: "tokens.css",
        format: "css/variables",
        options: { selector: ":root" }
      }]
    },
    scss: {
      transformGroup: "scss",
      buildPath: "build/scss/",
      files: [{ destination: "_tokens.scss", format: "scss/variables" }]
    },
    js: {
      transformGroup: "js",
      buildPath: "build/js/",
      files: [{ destination: "tokens.js", format: "javascript/es6" }]
    },
    json: {
      transformGroup: "js",
      buildPath: "build/json/",
      files: [{ destination: "tokens.json", format: "json/flat" }]
    }
  }
};
