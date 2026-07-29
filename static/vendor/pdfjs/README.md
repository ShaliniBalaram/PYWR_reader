# Vendored: PDF.js

`pdf.min.mjs` and `pdf.worker.min.mjs` are [Mozilla PDF.js](https://mozilla.github.io/pdf.js/)
**v4.6.82**, under the **Apache License 2.0**.

They are vendored (not loaded from a CDN) so the app stays offline and
self-contained — no build step, no network at runtime. They are imported
*lazily*, only when someone opens a PDF as a trace background, so nobody who
never does pays for the download.

To update: replace both files with a matching pair from the same PDF.js
release (the main build and its worker must be the same version), and bump the
version noted above.
