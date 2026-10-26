"""Exercise packaging and failure isolation with a fake Moodle toolchain, offline."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile


TOOLS = Path(__file__).resolve().parents[1]


class PrepareZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plugin zip ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tools = self.root / "repo/plugin/devtools"
        self.tools.mkdir(parents=True)
        for name in ("prepare_zip.sh", "build_zip.sh"):
            shutil.copy2(TOOLS / name, self.tools / name)
        self.source = self.tools.parent / "plugin"
        (self.source / "amd/src").mkdir(parents=True)
        (self.source / "amd/build").mkdir()
        (self.source / "version.php").write_text("<?php\n$plugin->release = '9.9.9';\n")
        for module in ("lab", "vnc", "novnc"):
            (self.source / f"amd/src/{module}.js").write_text("export const value = 1;\n")
        (self.source / "amd/build/stale.min.js").write_text("old build")
        self.cache = self.root / "build cache"
        self.moodle = self.cache / "moodle"
        (self.moodle / "public/mod").mkdir(parents=True)
        (self.moodle / ".gns3lab-build-workspace").touch()
        for name in ("package.json", "package-lock.json"):
            (self.moodle / name).write_text("{}")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        GNS3LAB_BUILD_CACHE=str(self.cache), TEST_ROOT=str(self.root),
                        TEST_PLUGIN=str(self.source))
        ref = re.search(r"^moodle_ref=(\w+)$", (TOOLS / "prepare_zip.sh").read_text(), re.M)[1]
        self.executable("git", f"#!/bin/sh\nprintf '%s\\n' '{ref}'\n")
        self.executable("node", "#!/bin/sh\n[ \"$1\" != --version ] || echo v22.11.0\nexit 0\n")
        self.executable("npm", '''#!/bin/sh
if [ "$1" = --version ]; then echo 10.9.0; exit 0; fi
[ "$1" = ci ] || exit 91
echo ci >> "$TEST_ROOT/npm-calls"
mkdir -p node_modules/.bin
cp "$TEST_ROOT/fake-grunt" node_modules/.bin/grunt
chmod +x node_modules/.bin/grunt
''')
        (self.root / "fake-grunt").write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
if os.environ.get('FAIL_GRUNT'):
    sys.exit(42)
root = Path('public/mod/gns3lab/amd')
(root / 'build').mkdir()
for source in (root / 'src').glob('*.js'):
    (root / 'build' / (source.stem + '.min.js')).write_text('built ' + source.read_text())
    if not os.environ.get('MISSING_MAP'):
        (root / 'build' / (source.stem + '.min.js.map')).write_text('{}')
if os.environ.get('EDIT_SOURCE'):
    (Path(os.environ['TEST_PLUGIN']) / 'amd/src/lab.js').write_text('edited during build')
''')
        self.archive = self.source.parent / "dist/mod_gns3lab-9.9.9.zip"

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_build(self, **env):
        return subprocess.run(["bash", str(self.tools / "prepare_zip.sh")],
                              env=dict(self.env, **env), text=True, capture_output=True)

    def test_builds_current_sources_removes_stale_files_and_reuses_dependencies(self):
        first = self.run_build()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertTrue(all(name.startswith("gns3lab/") for name in archive.namelist()))
            self.assertIn("gns3lab/amd/build/novnc.min.js", archive.namelist())
            self.assertNotIn("gns3lab/amd/build/stale.min.js", archive.namelist())
            self.assertIsNone(archive.testzip())
        (self.source / "amd/src/lab.js").write_text("export const value = 2;")
        second = self.run_build()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("value = 2", (self.source / "amd/build/lab.min.js").read_text())
        self.assertEqual((self.root / "npm-calls").read_text(), "ci\n")

    def test_failed_lint_preserves_build_and_previous_zip(self):
        self.archive.parent.mkdir()
        self.archive.write_bytes(b"previous archive")
        result = self.run_build(FAIL_GRUNT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.archive.read_bytes(), b"previous archive")
        self.assertEqual((self.source / "amd/build/stale.min.js").read_text(), "old build")

    def test_supports_moodle_shrinkwrap_without_package_lock(self):
        (self.moodle / "package-lock.json").rename(self.moodle / "npm-shrinkwrap.json")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.archive.exists())

    def test_missing_build_output_prevents_packaging(self):
        result = self.run_build(MISSING_MAP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing or empty build output", result.stderr)
        self.assertFalse(self.archive.exists())

    def test_concurrent_source_edit_requires_rebuild(self):
        result = self.run_build(EDIT_SOURCE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sources changed", result.stderr)
        self.assertFalse(self.archive.exists())

    def test_refuses_configured_moodle(self):
        (self.moodle / "config.php").touch()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configured Moodle", result.stderr)
        self.assertFalse((self.root / "npm-calls").exists())

    def test_zip_failure_preserves_previous_archive(self):
        self.archive.parent.mkdir()
        self.archive.write_bytes(b"previous archive")
        self.executable("zip", "#!/bin/sh\necho partial\nexit 7\n")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.archive.read_bytes(), b"previous archive")


if __name__ == "__main__":
    unittest.main()
