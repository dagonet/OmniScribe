#!/usr/bin/env bash
# PreToolUse hook: block PR merge/auto-merge without a fresh gate artifact
# Matcher: mcp__MCP_DOCKER__merge_pull_request|mcp__github-tools__github_pr_auto_merge
#
# Requires .gate/last-pass.json (written by hooks/run-gate.sh) at the repo
# toplevel of the merging session's cwd — worktree-aware, since developer
# agents self-merge from their worktrees. Blocks (exit 2) unless:
#   - the artifact exists,
#   - its "sha" equals the current HEAD of that checkout, and
#   - the artifact file is younger than 60 minutes (mtime).
#
# No-op (exit 0) when PROJECT_CONTEXT.md has no Gate command or the field is
# still a {{...}} placeholder — same graceful degradation as pre-commit-test.sh.

TOOL_INPUT=$(cat)

# Hook stdin JSON carries the session cwd at top level; fall back to $(pwd).
CWD=$(node -e "const j=JSON.parse(process.argv[1]); console.log(j.cwd||'')" "$TOOL_INPUT" 2>/dev/null)
if [ -z "$CWD" ] || [ ! -d "$CWD" ]; then
  CWD=$(pwd)
fi

REPO_TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_TOP" ]; then
  # Not a git checkout (nothing to gate against) — allow.
  exit 0
fi

# Read Gate command from PROJECT_CONTEXT.md. Tolerates: leading "- " / "* " list
# markers, the "**Gate Command**:" label style (java/python variants), and
# surrounding backticks — several variants write commands as `cmd`.
GATE_CMD=$(grep -E '^[-*[:space:]]*\*\*Gate( Command)?\*\*:' "$REPO_TOP/PROJECT_CONTEXT.md" 2>/dev/null | sed 's/.*\*\*Gate\( Command\)\?\*\*:[[:space:]]*//;s/[[:space:]]*$//;s/^`//;s/`$//' | head -1)

# No-op: no PROJECT_CONTEXT.md or no Gate command configured
if [ -z "$GATE_CMD" ]; then
  exit 0
fi

# No-op: placeholder not yet filled in
case "$GATE_CMD" in
  *\{\{*\}\}*) exit 0 ;;
esac

ARTIFACT="$REPO_TOP/.gate/last-pass.json"

if [ ! -f "$ARTIFACT" ]; then
  echo "BLOCKED: No gate artifact found. Run 'bash hooks/run-gate.sh' on the PR branch head (green gate writes .gate/last-pass.json), then merge." >&2
  exit 2
fi

ARTIFACT_SHA=$(grep -o '"sha":"[^"]*"' "$ARTIFACT" | head -1 | sed 's/"sha":"//;s/"$//')
HEAD_SHA=$(git -C "$CWD" rev-parse HEAD 2>/dev/null)

if [ -z "$ARTIFACT_SHA" ] || [ "$ARTIFACT_SHA" != "$HEAD_SHA" ]; then
  echo "BLOCKED: Gate artifact is stale (artifact sha: ${ARTIFACT_SHA:-none}, HEAD: $HEAD_SHA). Re-run 'bash hooks/run-gate.sh' on the current head, then merge." >&2
  exit 2
fi

# Freshness: artifact file mtime < 60 minutes (mtime avoids date-parsing portability issues)
ARTIFACT_EPOCH=$(stat -c %Y "$ARTIFACT" 2>/dev/null || stat -f %m "$ARTIFACT" 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
AGE=$((NOW_EPOCH - ARTIFACT_EPOCH))
if [ "$ARTIFACT_EPOCH" -eq 0 ] || [ "$AGE" -gt 3600 ]; then
  echo "BLOCKED: Gate artifact expired (${AGE}s old, max 3600s). Re-run 'bash hooks/run-gate.sh', then merge." >&2
  exit 2
fi

exit 0
