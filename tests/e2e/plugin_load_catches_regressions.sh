#!/usr/bin/env bash
# Puts each past plugin defect back, one at a time, and expects plugin_load.sh to fail.
# Without this, a load test that always passes would look the same as one that works.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
work="$(mktemp -d)"
status=0

break_and_expect_failure() {
  local name="$1" edit="$2" copy="$work/$1"
  mkdir -p "$copy"
  (cd "$repo" && tar cf - --exclude=.git --exclude=tests .) | (cd "$copy" && tar xf -)
  (cd "$copy" && python3 -c "$edit")
  if bash "$here/plugin_load.sh" "$copy" > "$work/$name.log" 2>&1; then
    echo "FAIL  not caught: $name"
    status=1
  else
    echo "PASS  caught: $name"
    grep '^FAIL' "$work/$name.log" | sed 's/^/        /'
  fi
}

break_and_expect_failure "hooks-json-missing-its-hooks-wrapper" "
import json
p = 'hooks/hooks.json'
d = json.load(open(p))
json.dump(d['hooks'], open(p, 'w'))
"
break_and_expect_failure "hook-using-a-variable-that-does-not-exist" "
p = 'hooks/hooks.json'
s = open(p).read().replace('CLAUDE_PLUGIN_ROOT', 'CLAUDE_PLUGIN_DIR')
open(p, 'w').write(s)
"
# The third defect from 2026-08-07, a manifest declaring `commands`, zeroed out the skill
# then. Claude Code 2.1.316 registers the skill anyway, so it can't be reproduced here, and
# tests/test_plugin.py keeps it out of the manifest instead. This one still proves the
# skill check can fail.
break_and_expect_failure "skill-file-missing" "
import os
os.remove('SKILL.md')
"
exit "$status"
