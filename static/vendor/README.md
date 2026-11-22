# Third-party files

Files in this folder are copied from other projects, unchanged.

| File | Project | Version | Licence |
|---|---|---|---|
| `htmx.min.js` | htmx (htmx.org) | 2.0.11 | Zero-Clause BSD |
| `jsQR.js` | jsQR (github.com/cozmo/jsQR) | 1.4.0 | Apache-2.0 |

`jsQR.js` reads QR codes from the camera in browsers without built-in barcode
detection (Windows, iPhone). The check-in page loads it only when needed.

To update htmx, replace the file with `dist/htmx.min.js` from the new release
and change the version here.
| `fonts/inter-*.woff2` | Inter (Fontsource variable) | 5.3.0 | SIL Open Font License 1.1 |
| `fonts/inter-tight-*.woff2` | Inter Tight (Fontsource variable) | 5.3.0 | SIL Open Font License 1.1 |

The fonts are served from our own server (not Google), so pages load in
Rwanda without calling another company and nobody's address is shared.
