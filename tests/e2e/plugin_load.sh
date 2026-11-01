#!/usr/bin/env bash
# Installs the plugin into a real Claude Code with an empty home folder, starts a real
# session against a fake API, and checks the skill registered and the startup hook ran.
# The plugin once failed to load and its hook never fired for three months, and nothing
# noticed. Validating the manifest wasn't enough, so this checks what actually happens.
#
# Usage: plugin_load.sh [plugin dir]    Needs: claude, python3, jq. CI only: it never
# reads a real login, because HOME and the API URL both point somewhere fake.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
plugin="$(cd "${1:-$here/../..}" && pwd)"
work="$(mktemp -d)"
fails=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; fails=$((fails + 1)); }

python3 -u "$here/fake_anthropic_api.py" > "$work/port" &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null' EXIT
for _ in $(seq 1 50); do [ -s "$work/port" ] && break; sleep 0.1; done

# The hook runs `python3 .../setup.py --check --json`. A logging python3 first on PATH
# proves it ran, without changing what the hook does.
real_python="$(command -v python3)"
mkdir -p "$work/bin" "$work/home" "$work/project"
cat > "$work/bin/python3" <<WRAP
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$work/python-calls"
exec "$real_python" "\$@"
WRAP
chmod +x "$work/bin/python3"

export HOME="$work/home"
export PATH="$work/bin:$PATH"
export ANTHROPIC_API_KEY="not-a-real-key"
ANTHROPIC_BASE_URL="http://127.0.0.1:$(cat "$work/port")"
export ANTHROPIC_BASE_URL
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_AUTOUPDATER=1
unset OPENAI_API_KEY GROQ_API_KEY
cd "$work/project" || exit 2

echo "Claude Code $(claude --version)"

if claude plugin validate "$plugin" > "$work/validate.txt" 2>&1; then
  pass "claude plugin validate"
else
  fail "claude plugin validate"; cat "$work/validate.txt"
fi

claude plugin marketplace add "$plugin" > "$work/marketplace.txt" 2>&1 || cat "$work/marketplace.txt"
if claude plugin install watch@watch > "$work/install.txt" 2>&1; then
  pass "plugin installs from the marketplace"
else
  fail "plugin installs from the marketplace"; cat "$work/install.txt"
fi

claude plugin list --json > "$work/list.json" 2>"$work/list.err" || cat "$work/list.err"
if jq -e 'map(select(.id == "watch@watch" and .enabled == true and ((.errors // []) | length == 0))) | length == 1' "$work/list.json" >/dev/null 2>&1; then
  pass "plugin list shows it enabled with no errors"
else
  fail "plugin list shows it enabled with no errors"; cat "$work/list.json"
fi

claude -p "say ok" --output-format stream-json --verbose --max-turns 1 > "$work/session.jsonl" 2>"$work/session.err"
echo "session exit code: $?"

init="$(jq -c 'select(.type == "system" and .subtype == "init")' "$work/session.jsonl" 2>/dev/null | head -1)"
echo "init: skills=$(printf '%s' "$init" | jq -c '[.skills[]? | select(test("watch"))]') commands=$(printf '%s' "$init" | jq -c '[.slash_commands[]? | select(test("watch"))]')"
if printf '%s' "$init" | jq -e '.skills // [] | map(select(test("(^|:)watch$"))) | length > 0' >/dev/null 2>&1; then
  pass "the watch skill is registered in a real session"
else
  fail "the watch skill is registered in a real session"
  printf '%s\n' "$init" | cut -c1-1500
  tail -5 "$work/session.err"
fi

if grep -q 'scripts/setup.py --check --json' "$work/python-calls" 2>/dev/null; then
  pass "the SessionStart hook ran the plugin's setup check"
else
  fail "the SessionStart hook ran the plugin's setup check"
  head -5 "$work/python-calls" 2>/dev/null
  cut -c1-400 "$work/session.jsonl" | head -8
fi

echo ""
echo "$fails failed"
[ "$fails" -eq 0 ]
