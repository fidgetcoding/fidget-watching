"""The repo's own links and manifests point at this fork, and credit upstream."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPO_URL = "https://github.com/fidgetcoding/fidget-watching"
OLD_ORG = "lorecraft-" + "io"  # Split so this file doesn't match its own check.


def _tracked_text_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel in out:
        path = ROOT / rel
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def test_no_tracked_file_points_at_the_old_org():
    hits = [rel for rel, text in _tracked_text_files() if OLD_ORG in text]
    assert hits == []


def test_manifests_parse_and_link_to_this_repo():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    codex = json.loads((ROOT / ".codex-plugin" / "skill.json").read_text())
    assert plugin["homepage"] == REPO_URL
    assert codex["homepage"] == REPO_URL
    skill = (ROOT / "SKILL.md").read_text()
    assert f"homepage: {REPO_URL}" in skill
    assert f"repository: {REPO_URL}" in skill


def test_marketplace_names_this_fork_and_credits_upstream():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["owner"]["name"] == "fidgetcoding"
    assert "devinilabs/claude-watch" in market["description"]


def test_readme_credits_upstream_and_license_is_unchanged():
    assert "https://github.com/devinilabs/claude-watch" in (ROOT / "README.md").read_text()
    assert "claude-watch contributors" in (ROOT / "LICENSE").read_text()
