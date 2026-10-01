#!/usr/bin/env bash
# PreToolUse tool-call budget for agents. Counters the runaway single spawn.
#
# Measured motivation: median 15 tool calls per agent, but 35 of 150 agents in one
# real session exceeded 60 and 19 exceeded 120, with a worst spawn of 417 calls.
# Nothing in the toolkit bounded a single spawn.
#
# Why blocking escalates (v1.2): v1.1 blocked exactly once at 120 and then only
# warned. The 417-call agent proves a single block does not stop a runaway, so the
# block now repeats every BLOCK_EVERY calls past BLOCK_AT.
#
# Contract (measured from live hook stdin):
#   Named teammates DO fire the project's PreToolUse and DO carry agent_id
#   (e.g. "aprobe-teammate-2-e14467006a486b91") plus agent_type. Main-thread
#   calls carry neither. So agent_id is a sound discriminator here, and the
#   agents that actually run away are reachable.
#
# Cost discipline: this fires on EVERY agent tool call, so the hot path is pure
# shell — one grep, no node, and an immediate exit on the main thread.
#
# PARSER-FREE BY CONSTRUCTION, and therefore silent about parsers (v2.2.1).
# The one field it needs is agent_id, and it reads it with a grep over the raw
# payload — it never sources hooks/lib/json.sh, so it does not need node,
# python3 or jq and never prints the `WARN: <hook>: no JSON parser on PATH`
# line the six fail-open hooks print. The v2.2.0 notes read as though every
# fail-open hook warns; this one has nothing to warn about. Do NOT add a warn
# call here: it would fire on every agent tool call for no enforcement gap.
#
# Posture: WARN once, then block on each threshold crossing. A hard wall would
# break legitimate large tasks; the goal is to force a deliberate reconsideration
# at each escalation. Wrap with the WARN-on-127 form in settings.json (exit 0) —
# a missing budget hook must never brick every tool call.
#
# CRITICAL: every threshold test uses -eq, never -ge. PreToolUse fires on every
# call, so a >= test would block calls 121, 122, 123 ... and the agent could never
# report its partial progress — the same unbounded-refire hazard the TeammateIdle
# ledger exists to prevent. The counter increments by exactly 1 per call, so each
# threshold is hit exactly once and calls between thresholds pass untouched.

set -u

WARN_AT=60
BLOCK_AT=120
BLOCK_EVERY=60

INPUT=$(cat 2>/dev/null || true)
[ -z "$INPUT" ] && exit 0

# Fast path: main thread has no agent_id -> not our business.
AGENT_ID=$(printf '%s' "$INPUT" | grep -o '"agent_id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -z "$AGENT_ID" ] && exit 0

SESSION=$(printf '%s' "$INPUT" | grep -o '"session_id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -z "$SESSION" ] && exit 0

# ---------------------------------------------------------------------------
# SendMessage IS EXEMPT — NOT COUNTED, NOT BLOCKED (v3.0.0, item B3).
#
# Measured: this hook blocked FIVE agent reports. It stopped agents FILING THEIR
# WORK, which is the exact failure the whole liveness effort exists to prevent —
# a budget guard whose worst outcome is that the agent it bounded can no longer
# tell anyone what it did. The block message itself says "report your partial
# result plus the blocker", and the tool that does that is the one it was
# blocking. That is a guard denying its own remedy, the same shape as the merge
# gate whose remediation manufactured a false receipt.
#
# THE CEILING IS KEPT, deliberately and against the temptation to soften it:
# spawns hit 417, 420 and 480 calls, so the escalating block is doing real work
# and stays exactly as it was. This narrows WHICH calls it applies to, not how
# hard it applies.
#
# NOT COUNTED, not merely not-blocked, and the distinction is the whole design.
# Exempting only the block would let a SendMessage land on call 120 and CONSUME
# that threshold — the `-eq` test fires once per exact value, so the ceiling
# would be silently skipped and the next block deferred to 180. Leaving the
# counter untouched means the next working call still lands on 120 and still
# blocks. Filing your work does not spend your budget, and it does not buy you
# extra budget either.
TOOL_NAME=$(printf '%s' "$INPUT" | grep -o '"tool_name":"[^"]*"' | head -1 | cut -d'"' -f4)
[ "$TOOL_NAME" = "SendMessage" ] && exit 0

# Kill switch, mirroring the other guards. ROOT is reused for the audit log.
ROOT=""
HOOK_CWD=$(printf '%s' "$INPUT" | grep -o '"cwd":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "${HOOK_CWD:-}" ]; then
  ROOT=$(printf '%s' "$HOOK_CWD" | tr '\\' '/')
  [ -f "$ROOT/.claude/liveness-off" ] && exit 0
fi

# Audit trail. Threshold events ONLY -- this hook runs on every tool call, so
# logging each pass would add a second write to the hot path and ~10,000 lines of
# no signal per session. The counter file already proves the hook ran.
# Best-effort: never allowed to change the exit code.
log_event() {
  [ -n "$ROOT" ] || return 0
  [ -d "$ROOT/.claude" ] || return 0
  printf '%s agent-budget-warn agent=%s calls=%s action=%s\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo unknown-time)" \
    "$AGENT_ID" "$N" "$1" >> "$ROOT/.claude/liveness.log" 2>/dev/null || true
  return 0
}

# Sanitize: agent_id is used as a filename.
SAFE=$(printf '%s' "$AGENT_ID" | tr -c 'A-Za-z0-9._-' '_')
DIR="${TMPDIR:-/tmp}/claude-agent-budget/$SESSION"
mkdir -p "$DIR" 2>/dev/null || exit 0
COUNTER="$DIR/$SAFE"

N=0
[ -f "$COUNTER" ] && N=$(cat "$COUNTER" 2>/dev/null || echo 0)
case "$N" in ''|*[!0-9]*) N=0 ;; esac
N=$((N + 1))
printf '%s' "$N" > "$COUNTER" 2>/dev/null || exit 0

# Block on each threshold crossing: BLOCK_AT, then every BLOCK_EVERY past it
# (120, 180, 240, ...). Strictly -eq / modulo-zero, so calls in between pass.
if [ "$N" -eq "$BLOCK_AT" ] || { [ "$N" -gt "$BLOCK_AT" ] && [ $(( (N - BLOCK_AT) % BLOCK_EVERY )) -eq 0 ]; }; then
  # v4.3.0 A3 -- a commit is the one call worth allowing at budget exhaustion:
  # hooks run in parallel, so blocking it after pre-commit-test ran wastes the
  # run AND loses the work. Parser-free: a raw match on the payload. A false
  # positive lets one non-commit call through once -- the brake still fires on
  # every other call. The command arrives JSON-escaped, so the prefix before
  # `git` accepts backslash escapes (`bash -c \"git commit ...\"`); the plan's
  # `[^"]*` stopped at the first \" and missed a quoted commit.
  #
  # Shape matched: `git` or `git.exe`, any number of `-x [arg]` options (an
  # arg is a space-free token or an escaped-quoted string, so `-C \"a b\"` and
  # `-c user.name=\"A B\"` work), then the word `commit`, which must END there
  # (space, quote, `;&|)` or a backslash escape) so `commit-graph` and
  # `commit-tree` are not commits.
  # KNOWN LIMITS (all fail toward BLOCKING, the safe side): an option arg in
  # single quotes containing a space (`-C 'a b'`), a `git` reached through an
  # alias or a variable (`$GIT commit`), `git --git-dir=x commit` with a space in
  # x, and a commit hidden behind a wrapper script. And a false positive lets
  # one non-commit call through once (`echo git commit`).
  BUD_CH='(\\"[^"]*\\"|[^[:space:]"\\])'
  BUD_CH1='(\\"[^"]*\\"|[^-[:space:]"\\])'
  BUD_OPT="[[:space:]]+-${BUD_CH}+([[:space:]]+${BUD_CH1}${BUD_CH}*)?"
  BUD_RX='"command":"([^"\\]|\\.)*\bgit(\.exe)?('"$BUD_OPT"')*[[:space:]]+commit([[:space:]";&|)\\]|$)'
  if { [ "$TOOL_NAME" = "Bash" ] || [ "$TOOL_NAME" = "PowerShell" ]; } \
     && printf '%s' "$INPUT" | grep -Eq "$BUD_RX"; then
    log_event commit-allowed
    # UN-COUNT the call, exactly as the SendMessage exemption above does not
    # count it. Otherwise the commit spends this threshold (-eq fires once per
    # value), the next block is BLOCK_EVERY calls away, and the only stop
    # signal is exit-0 stderr, which the model is unlikely to see. Un-counted,
    # the next non-commit call lands on the threshold again and is blocked.
    printf '%s' "$((N - 1))" > "$COUNTER" 2>/dev/null
    echo "BUDGET: $N tool calls -- this commit is allowed so the work is saved; every other call stays blocked. Report and stop after it." >&2
    exit 0
  fi
  log_event block
  cat >&2 <<EOF
BUDGET: this spawn has made $N tool calls (median is 15; 120 is the first ceiling).

Stop expanding and land what you have. Report your partial result plus the
blocker and let the PO re-tier the remainder — do not keep growing scope inside
one spawn. A long run is not evidence of progress.

You may continue past this, but it will stop you again every $BLOCK_EVERY calls.
Escape hatch: create .claude/liveness-off.
EOF
  exit 2
fi

# Single advisory warning at WARN_AT.
if [ "$N" -eq "$WARN_AT" ]; then
  log_event warn
  printf 'BUDGET: %s tool calls so far. Check that you are still inside your stated scope.\n' "$N" >&2
fi

exit 0
