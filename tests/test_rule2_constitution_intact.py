"""Rule 2 — the strategy may propose code changes but cannot modify production
code automatically. This is a governance rule enforced by process, so the test
pins the governance TEXT itself: the in-code constitution (safety.RULES, the
module docstring, and CLAUDE.md's table) must stay present and complete. Weaken
or delete a rule from the canonical copy and this fails."""

import pathlib

from src.engine import safety

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_rules_table_has_exactly_seven_rules():
    assert [r["n"] for r in safety.RULES] == [1, 2, 3, 4, 5, 6, 7]


def test_every_rule_names_its_enforcement():
    for r in safety.RULES:
        assert r["rule"].strip(), f"rule {r['n']} has empty text"
        assert r["enforced_by"].strip(), f"rule {r['n']} has no enforcement note"


def test_rule2_text_is_intact():
    rule2 = safety.RULES[1]
    assert rule2["n"] == 2
    assert "code" in rule2["rule"]
    assert "no code-editing capability" in rule2["enforced_by"]


def test_module_docstring_carries_the_constitution():
    doc = safety.__doc__
    assert "cannot modify production code" in doc
    assert "fail-closed" in doc.lower()
    for n in range(1, 8):
        assert f"{n}." in doc, f"rule {n} missing from the safety.py docstring"


def test_claude_md_still_documents_the_constitution():
    text = (REPO / "CLAUDE.md").read_text()
    assert "Trading Safety Constitution" in text
