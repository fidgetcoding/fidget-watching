"""Fast checks on the plugin files. tests/e2e/plugin_load.sh is the real test, in CI: it
installs the plugin into Claude Code and starts a session."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_hooks_json_has_the_required_wrapper_and_the_right_variable():
    data = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "${CLAUDE_PLUGIN_ROOT}" in command
    assert "CLAUDE_PLUGIN_DIR" not in command


def test_manifest_does_not_declare_commands():
    """Declaring commands zeroed out skill registration, 7 of 7 trials on 2026-08-07."""
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert "commands" not in manifest
    assert (ROOT / manifest["skills"] / "SKILL.md").exists()
    assert (ROOT / manifest["hooks"]).exists()


def test_marketplace_names_this_fork_and_credits_upstream():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["owner"]["name"] == "fidgetcoding"
    assert "devinilabs/claude-watch" in market["description"]


def test_startup_hook_runs_and_exits_clean(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GROQ_API_KEY")}
    env.update(HOME=str(tmp_path), CLAUDE_PLUGIN_ROOT=str(ROOT))
    proc = subprocess.run(
        ["bash", str(ROOT / "hooks" / "scripts" / "session_start.sh")],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "" or proc.stdout.startswith("[watch]")
