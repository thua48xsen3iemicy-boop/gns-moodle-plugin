"""The prompt for a neural network in the plugin's lab editor follows the checks format (docs/autochecks/README.md §7.8).

Each plugin/plugin/prompts/checks.*.md starts with `<!-- gns3lab-format: {...} -->`: the part of the format the prompt
describes. When the format moves on, this test fails until the prompt text and that line are updated together.
"""

import json
import re
from pathlib import Path

import pytest

from lab_manager.checks import prompt_fingerprint

PROMPTS = Path(__file__).resolve().parents[2] / "plugin" / "plugin" / "prompts"
LINE = re.compile(r"\A<!-- gns3lab-format: (\{.*\}) -->\n")
HEAD = re.compile(r"\A(?:<!--.*?-->\s*)+", re.S)  # The plugin shows the text after the leading comments


def prompts():
    return sorted(PROMPTS.glob("checks.*.md")) if PROMPTS.is_dir() else []


@pytest.mark.skipif(not prompts(), reason="the plugin is not in this tree (a release of the manager)")
@pytest.mark.parametrize("path", prompts(), ids=lambda p: p.name)
def test_the_prompt_knows_the_format(path):
    text = path.read_text(encoding="utf-8")
    found = LINE.match(text)
    assert found, f"{path.name} must start with <!-- gns3lab-format: {{...}} -->"
    assert json.loads(found[1]) == prompt_fingerprint(), (
        f"The checks format changed: update the text of {path} and its gns3lab-format line to "
        f"{json.dumps(prompt_fingerprint(), ensure_ascii=False)}")
    body = HEAD.sub("", text)
    for mark in ("[[LAB]]", "[[CHECKS]]", "[[TASK]]"):
        assert body.count(mark) == 1, f"{path.name}: {mark} once in the text"


def test_every_language_has_the_prompt():
    if prompts():
        assert {p.name for p in prompts()} >= {"checks.en.md", "checks.ru.md"}
