# pdf.js (vendored)

Used only by the Player Lookup page (`lookup/`) to read the text of a PDF **in the browser**.
The PDF never leaves the user's device. The files are loaded only when a PDF is chosen.

- Package: `pdfjs-dist` 6.5.438 (Mozilla), license Apache-2.0 (see `LICENSE`)
- Source: https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.5.438.tgz
- Tarball integrity (npm): `sha512-vg8Pw1x+AFKy4AMDvhuWl5cOYV8d698UoEANoFFhc79hqXDnZQqnOXoxw3HG907WHOg1JYOvLhP756boddFxpA==`
- Files: `build/pdf.min.mjs` -> `pdf.min.js`, `build/pdf.worker.min.mjs` -> `pdf.worker.min.js`. Content unmodified;
  only the extension is `.js`, so every static host serves them with a JavaScript content type (ES modules
  are refused when served as `application/octet-stream`).

To update: download the new tarball, check its integrity against the npm registry, and replace both
files together (the library and its worker must be the same version).
