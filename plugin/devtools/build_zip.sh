#!/bin/sh
# Builds dist/mod_gns3lab-<release>.zip for "Site administration > Plugins > Install plugins":
# one top-level folder named gns3lab, as the Moodle installer expects.
set -e
cd "$(dirname "$0")/.."
release=$(sed -n "s/^\$plugin->release *= *'\(.*\)';/\1/p" plugin/version.php)
case "$release" in
    ''|*[!a-zA-Z0-9._-]*) echo 'Invalid or missing plugin release in version.php' >&2; exit 1 ;;
esac
mkdir -p dist
tmp=$(mktemp -d "$(pwd)/dist/.build-zip.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
cp -r plugin "$tmp/gns3lab"
(cd "$tmp" && zip -qr -X - gns3lab) > "$tmp/plugin.zip"
# Publish only a complete archive; a failed zip must not truncate the previous one.
mv "$tmp/plugin.zip" "dist/mod_gns3lab-$release.zip"
echo "$(pwd)/dist/mod_gns3lab-$release.zip"
