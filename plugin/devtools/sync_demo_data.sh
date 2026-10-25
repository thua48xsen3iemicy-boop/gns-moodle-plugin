#!/bin/sh
# Copies the demo labs from fixtures/ into the plugin (plugin/demo is what the plugin shows until the manager is connected).
set -e
cd "$(dirname "$0")/.."
for lab in ospf-multi-area linux-switch-vlan; do
    for f in "$lab.gns3" "$lab.live.json" "$lab.facts.json" "$lab.facts-broken.json"; do
        cp "fixtures/$f" "plugin/demo/$f"
    done
done
echo "plugin/demo synced; the prototype keeps its own copies of the facts (see docs/live-state.md)"
