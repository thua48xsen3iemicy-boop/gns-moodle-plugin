# Student Canvas fonts

Unmodified complete WOFF2 fonts from the official IBM packages, including Cyrillic:

| npm package | Version | Files |
| --- | --- | --- |
| `@ibm/plex-mono` | 2.5.0 | Regular, Medium |
| `@ibm/plex-sans` | 1.1.0 | Regular, Medium, SemiBold |
| `@ibm/plex-sans-condensed` | 2.0.0 | Medium, SemiBold |

Source: https://github.com/IBM/plex (official packages from https://registry.npmjs.org/).
Original SIL Open Font License 1.1 notices are preserved in `ibm-plex/*-LICENSE.txt`.
Font files have not been edited or renamed.

`styles.css` uses Moodle's `[[font:mod_gns3lab|filename.woff2]]` URLs; Moodle 5.1
serves plugin fonts through `theme/font.php`. Filenames must be flat (no subdirectories).
The UI harness expands these URLs and copies the same files to its local font directory.
The student page makes no requests to Google Fonts or a font CDN.
