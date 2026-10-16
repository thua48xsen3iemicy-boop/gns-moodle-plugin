#!/bin/sh
# Builds dist/mod_gns3lab-<release>.zip for "Site administration > Plugins > Install plugins":
# one top-level folder named gns3lab, as the Moodle installer expects.
set -e
cd "$(dirname "$0")/.."
release=$(sed -n "s/^\$plugin->release *= *'\(.*\)';/\1/p" plugin/version.php)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cp -r plugin "$tmp/gns3lab"
mkdir -p dist
rm -f "dist/mod_gns3lab-$release.zip"
(cd "$tmp" && zip -qr -X - gns3lab) > "dist/mod_gns3lab-$release.zip"
echo "dist/mod_gns3lab-$release.zip"
