#!/usr/bin/env bash
# PreToolUse hook: block Read calls on files larger than the threshold.
#
# Matcher: Read
# Intent: force large-file analysis through ctx_execute_file or Explore
# subagents instead of loading raw file contents into PO context.
# The 22% Read-tool share measured in docs/plans/2026-04-14-context-baseline.md
# is the motivating bucket.
#
# Decision rule:
#   effective_lines = min(file_line_count, limit or file_line_count)
#   effective_lines <= THRESHOLD -> allow (exit 0)
#   effective_lines >  THRESHOLD -> block (exit 2 with stderr diagnostic)
#
# Missing files and unreadable paths are allowed; Read will produce its own
# error. Log append to ~/.claude/state/read-size-gate.log is best-effort:
# log write failures never mask the block/allow decision.

THRESHOLD=500
LOG_FILE="$HOME/.claude/state/read-size-gate.log"

TOOL_INPUT=$(cat)

# Extract tool_name, file_path, offset, limit from JSON stdin.
# Matches tier-before-coder.sh style (node -e inline parse).
TOOL_NAME=$(node -e "try{console.log(JSON.parse(process.argv[1]).tool_name||'')}catch(e){}" "$TOOL_INPUT" 2>/dev/null || echo '')
if [ "$TOOL_NAME" != "Read" ]; then
  exit 0
fi

FILE_PATH=$(node -e "try{console.log(JSON.parse(process.argv[1]).tool_input?.file_path||'')}catch(e){}" "$TOOL_INPUT" 2>/dev/null || echo '')
OFFSET=$(node -e "try{var v=JSON.parse(process.argv[1]).tool_input?.offset;console.log(v==null?'':v)}catch(e){}" "$TOOL_INPUT" 2>/dev/null || echo '')
LIMIT=$(node -e "try{var v=JSON.parse(process.argv[1]).tool_input?.limit;console.log(v==null?'':v)}catch(e){}" "$TOOL_INPUT" 2>/dev/null || echo '')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Normalize to absolute path. realpath is present in Git Bash (GNU coreutils)
# and on Linux/macOS. If the file doesn't exist yet, let Read handle it.
ABS_PATH=$(realpath "$FILE_PATH" 2>/dev/null || echo "$FILE_PATH")
if [ ! -f "$ABS_PATH" ] || [ ! -r "$ABS_PATH" ]; then
  exit 0
fi

FILE_LINES=$(wc -l < "$ABS_PATH" 2>/dev/null | tr -d ' ')
if [ -z "$FILE_LINES" ] || ! [ "$FILE_LINES" -ge 0 ] 2>/dev/null; then
  exit 0
fi

# effective_lines = min(file_lines, limit or file_lines)
if [ -n "$LIMIT" ] && [ "$LIMIT" -ge 0 ] 2>/dev/null; then
  if [ "$LIMIT" -lt "$FILE_LINES" ]; then
    EFFECTIVE_LINES="$LIMIT"
  else
    EFFECTIVE_LINES="$FILE_LINES"
  fi
else
  EFFECTIVE_LINES="$FILE_LINES"
fi

HAS_OFFSET="false"
[ -n "$OFFSET" ] && HAS_OFFSET="true"
HAS_LIMIT="false"
[ -n "$LIMIT" ] && HAS_LIMIT="true"

# Best-effort log append. Failures never block the decision.
log_decision() {
  local action="$1"
  if [ "$action" = "ALLOW" ] && [ "$FILE_LINES" -le 250 ]; then
    return 0
  fi
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown")
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
  # Subshell wraps the redirection so both printf stderr and the shell's
  # own "permission denied" from a failed >> open are swallowed.
  ( printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$ts" "$action" "$EFFECTIVE_LINES" "$FILE_LINES" "$HAS_OFFSET" "$HAS_LIMIT" "$ABS_PATH" \
      >> "$LOG_FILE" ) 2>/dev/null || true
}

if [ "$EFFECTIVE_LINES" -le "$THRESHOLD" ]; then
  log_decision "ALLOW"
  exit 0
fi

log_decision "BLOCK"

{
  echo "BLOCKED: Read on $ABS_PATH ($EFFECTIVE_LINES effective lines, threshold=$THRESHOLD)"
  echo ""
  echo "This file is too large to Read directly. Use one of:"
  echo "  1. mcp__plugin_context-mode_context-mode__ctx_execute_file(path, language, code)"
  echo "     for analysis -- only your printed summary enters context."
  echo "  2. Explore subagent -- returns a compressed summary."
  echo "  3. Read(path, offset=X, limit=<=$THRESHOLD) -- range-targeted read."
  echo ""
  echo "If you are about to Edit this file and need to locate your target region,"
  echo "run ctx_execute_file with a grep snippet to find the line numbers first,"
  echo "then use offset/limit to read just that slice."
  echo ""
  echo "Hook config: ~/.claude/hooks/read-size-gate.sh (threshold=$THRESHOLD)"
  echo "To disable temporarily: comment the hook in ~/.claude/settings.json."
} >&2

exit 2