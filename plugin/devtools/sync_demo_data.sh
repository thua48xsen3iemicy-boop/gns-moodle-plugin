#!/bin/sh
# Copies the demo lab from fixtures/ into the plugin (plugin/demo is what the plugin shows until the manager is connected).
set -e
cd "$(dirname "$0")/.."
for f in ospf-multi-area.gns3 ospf-multi-area.live.json ospf-multi-area.facts.json ospf-multi-area.facts-broken.json; do
    cp "fixtures/$f" "plugin/demo/$f"
done
echo "plugin/demo synced; the prototype keeps its own copies of the facts (see docs/live-state.md)"
