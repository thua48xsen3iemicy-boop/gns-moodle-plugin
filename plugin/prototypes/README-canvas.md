# Student canvas prototype

Open `student-ui-canvas.html` together with its two sibling JavaScript files.
For a local HTTP preview, run from `plugin/`:

```sh
python3 -m http.server 18765 --bind 127.0.0.1
```

Visit `http://127.0.0.1:18765/prototypes/student-ui-canvas.html`.
The topology, collection states, console output and assignments are local examples.
There is no Moodle or manager connection. xterm and fonts use the existing CDN links;
the inspection-command buttons also work without xterm.

The canvas has L1/L2/L3 and a reserved L4 tab. OSPF has no canvas view in this iteration.
Use **Обзор** for the layer's objects and findings, **Задания** for the local checklist,
and **Режимы** for fresh/stale/missing/failed collection, VLAN/IP faults, MAC table
states, the student `live`/`seen`/`fog` views and the teacher preview.
In exploration mode, inspect the initial silhouette and run an inspection command
from its card or its simulated console. The local discovery mask survives reloads.

Selecting a device highlights its connections and neighbours on the current layer.
On L2, a selected VLAN traces its broadcast domain. On L3, selecting a subnet
highlights all its member devices and interface edges; selecting a device traces
its subnets and their peers. Unrelated objects are dimmed, while fault colours and
the discovery mask are preserved. Closing the object card leaves the selection
and its connection highlight in place. Click the background or press Escape to
clear the selection. Node rings become finer at overview zoom and stay bounded
when zooming in; subnet borders and highlighted edges use lightweight strokes.

On L1, each physical connection has two directed rails: TX at one endpoint joins
RX at the other, and the return rail joins the opposite TX/RX pair. Endpoint roles
are attached to their own rail with short leaders. Their text stays horizontal
on horizontal, vertical and diagonal links. Interface names use the collision
layout around those fixed role labels. At overview zoom or on a short rail,
role labels are suppressed rather than detached from their connection; the link
card still lists both transmitter/receiver pairs.

`student-ui-canvas-workbench.js` adapts the canvas to the production data models.
`student-ui-canvas-core.js` is generated from the plugin's **unchanged** pure modules
`model.js`, `facts.js`, `consolefacts.js` and `reveal.js`. It preserves their licenses
and avoids a separate implementation of subnet diagnostics or discovery rules.
After changes to those modules, rebuild and check the snapshot:

```sh
python3 devtools/build_canvas_core.py
python3 devtools/build_canvas_core.py --check
```

The browser regression check uses Python Playwright with Chromium. From `plugin/`:

```sh
python3 devtools/check_canvas_prototype.py
```

Set `CHROMIUM_BIN` to override `/usr/bin/chromium`. The check serves the files on a
temporary local port and blocks external resources. It checks shared-model subnet
diagnostics, navigation, masks, console-derived facts, collection and MAC states,
teacher preview, reserved L4, responsive cards and fixed-size link markers.
