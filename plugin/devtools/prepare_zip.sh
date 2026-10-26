#!/usr/bin/env bash
# Build with Moodle's own toolchain, then package the current working copy.
set -euo pipefail
trap 'echo "Build failed; do not install a ZIP from an earlier run." >&2' ERR

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
plugin_dir=$(cd -- "$script_dir/../plugin" && pwd -P)
moodle_ref=e55a69716177993e6cdcf58b8152862da1ae5924
nvm_ref=v0.40.3
cache=${GNS3LAB_BUILD_CACHE:-"$script_dir/.cache"}

if [[ ${1:-} == --help ]]; then
    cat <<'HELP'
Usage: bash plugin/devtools/prepare_zip.sh

Builds the current plugin with Moodle 5.1 (ESLint + grunt amd), updates
plugin/plugin/amd/build, and writes plugin/dist/mod_gns3lab-<release>.zip.
Requires Bash, Git, curl, Python 3, zip and flock (util-linux).
Uses Node >=22.11 <23, or installs Node 22 through a private nvm cache.
No sudo, running Moodle, PHP, database, or system Node changes are needed.
GNS3LAB_BUILD_CACHE overrides the private cache directory.
HELP
    exit 0
fi
[[ $# == 0 ]] || { echo 'Unknown argument; use --help.' >&2; exit 1; }
for tool in git curl python3 zip flock; do
    command -v "$tool" >/dev/null || { echo "Missing command: $tool" >&2; exit 1; }
done
mkdir -p -- "$cache"
cache=$(cd -- "$cache" && pwd -P)
exec 9>"$cache/build.lock"
flock -n 9 || { echo 'Another plugin build is using this cache.' >&2; exit 1; }

node_ok() {
    command -v node >/dev/null && command -v npm >/dev/null &&
        node -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a===22 && b>=11 ? 0 : 1)'
}
if ! node_ok; then
    echo 'Preparing private Node 22 (system Node is unchanged)...'
    export NVM_DIR="$cache/nvm"
    if [[ ! -e "$NVM_DIR" ]]; then
        git clone --quiet --depth 1 --branch "$nvm_ref" https://github.com/nvm-sh/nvm.git "$NVM_DIR"
    fi
    [[ -f "$NVM_DIR/nvm.sh" ]] || { echo "Incomplete nvm cache: $NVM_DIR" >&2; exit 1; }
    # A system/global npm prefix must not redirect the private nvm installation.
    # This changes only the script process, never the caller's environment or npm config.
    unset NPM_CONFIG_PREFIX npm_config_prefix PREFIX
    # nvm is sourced, not installed into shell profiles; its functions expect nounset to be off.
    set +u
    source "$NVM_DIR/nvm.sh" --no-use
    nvm install 22
    nvm use 22
    set -u
fi
node_ok || { echo 'Node >=22.11 <23 and npm are required.' >&2; exit 1; }
echo "Using Node $(node --version), npm $(npm --version)"

moodle="$cache/moodle"
if [[ ! -e "$moodle" ]]; then
    # An interrupted download must not masquerade as a usable checkout on the next run.
    clone=$(mktemp -d "$cache/moodle-clone.XXXXXX")
    git -C "$clone" init --quiet
    git -C "$clone" remote add origin https://github.com/moodle/moodle.git
    git -C "$clone" fetch --quiet --depth 1 origin "$moodle_ref"
    git -C "$clone" checkout --quiet --detach FETCH_HEAD
    touch "$clone/.gns3lab-build-workspace"
    mv -- "$clone" "$moodle"
fi
[[ -f "$moodle/.gns3lab-build-workspace" && ! -L "$moodle" ]] || {
    echo "Not a private build checkout: $moodle" >&2; exit 1;
}
[[ $(git -C "$moodle" rev-parse HEAD) == "$moodle_ref" ]] || {
    echo 'Moodle cache revision differs; use a fresh GNS3LAB_BUILD_CACHE directory.' >&2; exit 1;
}
# public/config.php is a tracked loader in Moodle 5.1; the actual site config is at the root.
[[ ! -e "$moodle/config.php" ]] || {
    echo 'Refusing to use a configured Moodle installation for packaging.' >&2; exit 1;
}
cd -- "$moodle"
# Reuse dependencies only for the same lockfile and Node/npm combination.
fingerprint=$(python3 - "$moodle_ref" "$(node --version)" "$(npm --version)" <<'PY'
import hashlib
import sys
from pathlib import Path
h = hashlib.sha256('\n'.join(sys.argv[1:]).encode())
lockfile = 'npm-shrinkwrap.json' if Path('npm-shrinkwrap.json').is_file() else 'package-lock.json'
for name in ('package.json', lockfile):
    h.update(Path(name).read_bytes())
print(h.hexdigest())
PY
)
if [[ ! -x node_modules/.bin/grunt || ! -f .gns3lab-npm-ready ]] ||
        [[ $(cat .gns3lab-npm-ready) != "$fingerprint" ]]; then
    rm -f .gns3lab-npm-ready
    echo 'Installing Moodle build dependencies with npm ci...'
    npm ci --no-fund --no-audit
    printf '%s\n' "$fingerprint" > .gns3lab-npm-ready
fi

target="$moodle/public/mod/gns3lab"
# A real copy is needed: ESLint resolves configuration through the physical parent paths.
# Clear the copied build so stale files cannot make an incomplete build pass validation.
python3 - "$plugin_dir" "$target" <<'PY'
import shutil
import sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
if target.is_symlink():
    raise SystemExit('Refusing a symlink as the build plugin directory')
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
shutil.rmtree(target / 'amd/build', ignore_errors=True)
PY
echo 'Running Moodle ESLint and grunt amd...'
./node_modules/.bin/grunt amd --root=public/mod/gns3lab

# Validate every module, including third-party modules, before updating repository files.
python3 - "$plugin_dir" "$target" <<'PY'
import shutil
import sys
from pathlib import Path
source, target = map(Path, sys.argv[1:])
build = target / 'amd/build'
for module in (target / 'amd/src').rglob('*.js'):
    name = module.relative_to(target / 'amd/src').with_suffix('.min.js')
    for output in (build / name, build / (str(name) + '.map')):
        if not output.is_file() or output.stat().st_size == 0:
            raise SystemExit(f'Missing or empty build output: {output}')
# A concurrent edit must be rebuilt, not silently packaged with earlier JavaScript.
def sources(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob('*.js')}
if sources(source / 'amd/src') != sources(target / 'amd/src'):
    raise SystemExit('AMD sources changed during the build; run the script again')
destination = source / 'amd/build'
if destination.is_symlink():
    raise SystemExit('Refusing a symlink as the repository build directory')
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(build, destination)
PY
echo 'AMD files updated in plugin/plugin/amd/build.'
bash "$script_dir/build_zip.sh"
echo 'ZIP ready. Review and commit the AMD changes; install the ZIP through Moodle administration.'
