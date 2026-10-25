# Isoflow alpha viewer

This is an isolated Moodle `local_gns3isoflow` experiment. It does not replace or modify `mod_gns3lab`, call the lab manager, or open consoles.

## Run in Moodle 5.1

1. Copy this directory to `public/local/gns3isoflow` in a Moodle 5.1 installation.
2. Complete the Moodle plugin installation.
3. Sign in and open `/local/gns3isoflow/index.php`.

The page opens a bundled GNS3 example. The file picker accepts another `.gns3` JSON file and converts it locally in the browser. It does not upload the file.

Without Moodle, run `python3 -m http.server 8000` in this directory and open `http://localhost:8000/preview.html`.

## Rebuild the browser bundle

```sh
cd plugin/isoflow-alpha
npm ci
npm test
npm run build
npm run smoke
```

The build writes a self-contained Moodle AMD module to `amd/build/app.min.js`. This pre-alpha experiment deliberately carries React and Isoflow in that bundle; the installed `mod_gns3lab` remains vanilla AMD. The generated bundle is committed so Moodle can install this plugin without npm.
The smoke test uses a local Chromium executable (`CHROME_BIN` can override `/usr/bin/chromium`).

## Projection and current limits

- GNS3 `node_id` and `link_id` remain the IDs in the Isoflow model. Port numbers, device properties, and link filters stay in a separate browser-side lookup.
- GNS3 pixel positions are mapped onto Isoflow tiles; collisions move the later node to a nearby tile. The original document is not changed.
- Isoflow's public API does not expose node-selection callbacks. The side list provides a GNS3 detail inspector for this experiment.
- GNS3 drawings, port labels on links, live statuses, consoles, and L2/L3/OSPF layers are outside this first isolated preview. The page reports how many drawings it omitted.
- CPU and RAM are shown when present in `properties`; the sample `.gns3` has no GPU model field.

Isoflow 1.1.1 is MIT-licensed; the full dependency lockfile and generated bundle are kept in this directory.
License notices for the bundled Isoflow distribution and React are in `licenses/`.
