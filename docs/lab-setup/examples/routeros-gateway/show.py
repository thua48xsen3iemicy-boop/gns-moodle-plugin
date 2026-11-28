#!/usr/bin/env python3
"""Print the example's scripts as text to paste into a device console of the lab (README.md, "Stand test").

  python3 show.py steps R1     the setup steps of R1 wrapped as the manager sends them to the slot
  python3 show.py checks R1    the activity checks of R1, each printing its id and output
  python3 show.py checks PC1   the same for a Linux node, with the exit code of each check

No third-party modules: runs anywhere with Python 3. The wrappers below repeat lab_manager/labsetup.py;
manager/tests/test_setup_example.py checks that they stay the same.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = "gns3lab-setup"
SH_WRAPPER = ('e=$(mktemp)||e=/tmp/gns3lab$$\n(\n{run}\n)2>$e\n'
              'c=$?;cat $e>&2;[ $c = 0 ]||printf \'\\n%s\\n\' "' + MARKER + ' {id} exit $c: '
              '$(grep . $e|tail -1|cut -c-100)">&2;rm -f $e;exit $c\n')
ROUTEROS_WRAPPER = ':do {{\n{run}\n}} on-error={{:error "' + MARKER + ' {id} exit 1"}}\n'


def load(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def wrap(step):
    template = ROUTEROS_WRAPPER if step.get("shell") == "routeros" else SH_WRAPPER
    return template.format(run=step["run"], id=step["id"])


def steps(node):
    out = []
    for step in load("setup.json")["setup"]:
        if step["node"] == node:
            out.append(f"# ---- step {step['id']}: {step['title']}\n{wrap(step)}")
    return out


def checks(node):
    out = []
    for script in load("checks.json")["scripts"]:
        if script["node"] != node:
            continue
        if script.get("shell") == "routeros":
            # A block of its own: the :local names of one check do not reach the next one
            out.append(f':put "== {script["id"]}"\n{{\n{script["run"]}\n}}\n')
        else:
            out.append(f'echo "== {script["id"]}"\n(\n{script["run"]}\n)\necho "exit=$?"\n')
    return out


def main(argv):
    if len(argv) != 3 or argv[1] not in ("steps", "checks"):
        sys.exit(__doc__)
    parts = steps(argv[2]) if argv[1] == "steps" else checks(argv[2])
    if not parts:
        sys.exit(f"nothing for node {argv[2]!r}")
    print("\n".join(parts), end="")


if __name__ == "__main__":
    main(sys.argv)
