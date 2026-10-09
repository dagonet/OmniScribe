#!/usr/bin/env bash
# model-floor.sh -- PreToolUse(Agent): a spawn with no explicit model whose type
# has no model of its own (general-purpose, Plan, Explore, `model: inherit`)
# runs on the project default instead of the orchestrator's model (v4.3.0, spec
# Part C). Never touches an explicit model or a typed agent's own model. Steps
# aside while the Jev router will run in this checkout (it applies the same floor; see am_jev_routing in lib/agent-model.sh) and while the user
# has set CLAUDE_CODE_SUBAGENT_MODEL to a real model (S-30; see the step-aside
# below for exactly what that covers). ADVISORY: any doubt -> exit 0, no output (the spawn
# inherits, as before v4.3.0).
#
# The output is `updatedInput` ONLY -- deliberately no `permissionDecision`. A
# hook that answers "allow" skips the user's permission prompt; an advisory
# model floor must never grant permission. STDOUT is the whole contract: exactly
# one JSON object on success, nothing otherwise; the one diagnostic line goes to
# STDERR.
#
# Agent identity (S-22): Claude Code identifies an agent by its frontmatter
# `name:` and scans .claude/agents/ recursively -- the filename need not match.
# So the type is resolved by name across the project then the user-level agents
# tree, falling back to the filename. With NO file, only the known inheriting
# built-ins are floored; any other unknown type may come from --agents, managed
# settings or a plugin that this hook cannot see, so it does nothing.
#
# v4.4.0: the resolution (agent identity, the env step-aside, the built-in list,
# the project default) lives in lib/agent-model.sh, shared with the optional Jev
# router so both answer the same question the same way. With Jev off this hook
# is byte-for-byte the base release in behaviour: scripts/test-hooks.sh J-DIFF.
lib="$(dirname "$0")/lib/json.sh"
[ -f "$lib" ] || exit 0
# shellcheck source=lib/json.sh
. "$lib"
amlib="$(dirname "$0")/lib/agent-model.sh"
[ -f "$amlib" ] || exit 0
# shellcheck source=lib/agent-model.sh
. "$amlib"
MF_JSON=$(cat)
am_env_forced && exit 0
case "$MF_JSON" in "$JSON_BOM"*) MF_JSON=${MF_JSON#"$JSON_BOM"} ;; esac
json_fields "$MF_JSON" tool_name tool_input.model tool_input.subagent_type cwd; MF_RC=$?   # v4.4.0 C3: one parser run
[ "$MF_RC" = 2 ] && exit 0
if [ "$MF_RC" = 0 ]; then MF_TN=${JF[0]}; MF_MODEL=${JF[1]}; MF_TYPE=${JF[2]}; MF_CWD=${JF[3]}
else
  # invalid payloads never come from Claude Code; the fallback only preserves the old jq multi-document verdict
  json_valid "$MF_JSON" || exit 0
  MF_TN=$(json_get "$MF_JSON" tool_name); MF_MODEL=$(json_get "$MF_JSON" tool_input.model)
  MF_TYPE=$(json_get "$MF_JSON" tool_input.subagent_type); MF_CWD=$(json_get "$MF_JSON" cwd)
fi
[ "$MF_TN" = "Agent" ] || exit 0
[ -n "$MF_MODEL" ] && exit 0
[ -n "$MF_TYPE" ] || MF_TYPE=general-purpose
[ -n "$MF_CWD" ] || MF_CWD=.
am_resolve "$MF_TYPE" "$MF_CWD"
[ "$AM_KIND" = floor ] || exit 0
am_jev_routing "$AM_ROOT" && exit 0
MF_DEF=$AM_MODEL
# Emit with the backend json.sh selected. The payload goes in on stdin and the
# model as an argument -- never interpolated into program text. tool_input is
# copied whole (updatedInput REPLACES it), so unknown keys survive.
case "$JSON_PARSER" in
  node)
    # ti.model is set on the parsed object itself (no Object.assign copy): a
    # `__proto__` key is an OWN property after JSON.parse and stays one.
    MF_OUT=$(printf '%s' "$MF_JSON" | node -e '
      var p = JSON.parse(require("fs").readFileSync(0, "utf8").replace(/^﻿/, ""));
      var ti = p.tool_input;
      if (ti === null || typeof ti !== "object" || Array.isArray(ti)) process.exit(1);
      ti.model = process.argv[1];
      process.stdout.write(JSON.stringify({ hookSpecificOutput: {
        hookEventName: "PreToolUse", updatedInput: ti } }));
    ' "$MF_DEF" 2>/dev/null) || exit 0 ;;
  python3)
    # allow_nan=False: an overflowing number (1e400 -> inf) would otherwise be
    # written as the non-JSON token Infinity; raising leaves stdout empty.
    MF_OUT=$(printf '%s' "$MF_JSON" | python3 -c '
import json, sys
p = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace"))
ti = p.get("tool_input")
if not isinstance(ti, dict):
    sys.exit(1)
ti = dict(ti)
ti["model"] = sys.argv[1]
out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": ti}}, ensure_ascii=False, allow_nan=False)
sys.stdout.buffer.write(out.encode("utf-8"))
' "$MF_DEF" 2>/dev/null) || exit 0 ;;
  jq)
    MF_OUT=$(printf '%s' "$MF_JSON" | jq -jc --arg m "$MF_DEF" '
      if (.tool_input | type) != "object" then error("no tool_input")
      else {hookSpecificOutput: {hookEventName: "PreToolUse", updatedInput: (.tool_input + {model: $m})}} end
    ' 2>/dev/null) || exit 0 ;;
  *) exit 0 ;;
esac
[ -n "$MF_OUT" ] || exit 0
MF_OUT=${MF_OUT%$'\r'}
printf '%s' "$MF_OUT"
echo "model-floor: $MF_TYPE had no model -> $MF_DEF" >&2
exit 0
