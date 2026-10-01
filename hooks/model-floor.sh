#!/usr/bin/env bash
# model-floor.sh -- PreToolUse(Agent): a spawn with no explicit model whose type
# has no model of its own (general-purpose, Plan, Explore, `model: inherit`)
# runs on the project default instead of the orchestrator's model (v4.3.0, spec
# Part C). Never touches an explicit model or a typed agent's own model. Steps
# aside while Jev routing is on (it applies the same floor) and while the user
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
lib="$(dirname "$0")/lib/json.sh"
[ -f "$lib" ] || exit 0
# shellcheck source=lib/json.sh
. "$lib"
MF_JSON=$(cat)
# S-23/S-30: CLAUDE_CODE_SUBAGENT_MODEL is a native default only when it holds a
# REAL model (an alias or a full claude-* id); `inherit` or empty means unset.
# With CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1 it covers every spawn, so step aside
# here. Without FORCE it covers only general-purpose and an untyped spawn
# (checked once the type is known, below): it does not reach Plan, Explore,
# `claude` or an agent whose own model is `inherit`, and those keep the floor.
MF_ENV_REAL=""
case "${CLAUDE_CODE_SUBAGENT_MODEL:-}" in haiku|sonnet|opus|fable|claude-*) MF_ENV_REAL=1 ;; esac
[ -n "$MF_ENV_REAL" ] && [ "${CLAUDE_CODE_SUBAGENT_MODEL_FORCE:-}" = 1 ] && exit 0
case "$MF_JSON" in "$JSON_BOM"*) MF_JSON=${MF_JSON#"$JSON_BOM"} ;; esac
json_have || exit 0
json_valid "$MF_JSON" || exit 0
[ "$(json_get "$MF_JSON" tool_name)" = "Agent" ] || exit 0
[ -n "$(json_get "$MF_JSON" tool_input.model)" ] && exit 0
MF_TYPE=$(json_get "$MF_JSON" tool_input.subagent_type)
[ -n "$MF_TYPE" ] || MF_TYPE=general-purpose
[ -n "$MF_ENV_REAL" ] && [ "$MF_TYPE" = general-purpose ] && exit 0
case "$MF_TYPE" in *[!A-Za-z0-9_.-]*|.*) exit 0 ;; esac
# Types that carry a model of their own (statusline-setup: sonnet,
# claude-code-guide: haiku) or ignore a model override (fork): step aside.
case "$MF_TYPE" in statusline-setup|claude-code-guide|fork) exit 0 ;; esac
MF_CWD=$(json_get "$MF_JSON" cwd); [ -n "$MF_CWD" ] || MF_CWD=.
MF_ROOT=$(git -C "$MF_CWD" rev-parse --show-toplevel 2>/dev/null) || MF_ROOT="$MF_CWD"
# GC_KEY_PRE, defined locally (same text as hooks/lib/git-cmd.sh and run-gate.sh;
# sourcing git-cmd.sh here would cost ~57 ms per Agent spawn): a BOM on line 1
# must not hide the key. Check 21c-2 requires every PROJECT_CONTEXT.md anchor to use it.
GC_BOM=$(printf '\357\273\277')
GC_KEY_PRE="^(${GC_BOM})?[-*[:space:]]*"
# mf_fm <file> <key> -- a frontmatter value, unquoted, no whitespace, CR and a
# leading BOM tolerated. Empty when the key (or the frontmatter) is absent.
mf_fm() { awk -v k="$2" -v bom="$GC_BOM" 'NR==1&&index($0,bom)==1{$0=substr($0,length(bom)+1)} NR==1&&/^---/{f=1;next} f&&/^---/{exit} f&&index($0,k":")==1{sub(/^[^:]*:[[:space:]]*/,"");print;exit}' "$1" 2>/dev/null | tr -d '\r"'"'"'[:space:]'; }
# mf_find <agents dir> -- the first *.md under it (recursively) whose frontmatter
# name equals $MF_TYPE. grep narrows the candidates; mf_fm confirms the name is
# really in the frontmatter (a `name:` line in a body does not count).
mf_find() {
  [ -d "$1" ] || return 0
  grep -rlE "^name:[[:space:]]*[\"']?${MF_TYPE}[\"']?[[:space:]]*\$" --include='*.md' "$1" 2>/dev/null | while IFS= read -r mf_c; do
    if [ "$(mf_fm "$mf_c" name)" = "$MF_TYPE" ]; then printf '%s\n' "$mf_c"; break; fi
  done | head -1
}
MF_FILE=""
for mf_d in "$MF_ROOT/.claude/agents" "$HOME/.claude/agents"; do
  MF_FILE=$(mf_find "$mf_d")
  [ -n "$MF_FILE" ] && break
done
if [ -z "$MF_FILE" ]; then
  for mf_f in "$MF_ROOT/.claude/agents/$MF_TYPE.md" "$HOME/.claude/agents/$MF_TYPE.md"; do
    [ -f "$mf_f" ] && { MF_FILE=$mf_f; break; }
  done
fi
if [ -n "$MF_FILE" ]; then
  # Any model of its own -- an alias or a full id -- is the agent's choice;
  # only `inherit` (or none) falls through to the floor.
  case "$(mf_fm "$MF_FILE" model)" in ""|inherit) ;; *) exit 0 ;; esac
else
  # No file: only the known inheriting built-ins. Anything else may be defined
  # where this hook cannot look, and when it cannot tell it does nothing.
  case "$MF_TYPE" in general-purpose|Plan|Explore|claude) ;; *) exit 0 ;; esac
fi
MF_GD=$(git -C "$MF_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
[ -n "$MF_GD" ] && grep -Eq '"route"[[:space:]]*:[[:space:]]*true' "$MF_GD/jev/config.json" 2>/dev/null && exit 0
MF_DEF=$(grep -E "${GC_KEY_PRE}\*\*Subagent default model\*\*:" "$MF_ROOT/PROJECT_CONTEXT.md" 2>/dev/null | head -1 | sed -E 's/.*\*\*Subagent default model\*\*:[[:space:]]*//; s/[`[:space:]]//g')
case "$MF_DEF" in haiku|sonnet|opus|fable) ;; *) MF_DEF=sonnet ;; esac
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
