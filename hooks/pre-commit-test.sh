#!/usr/bin/env bash
# PreToolUse hook: require passing tests before git commit
# Matcher: mcp__git-tools__git_commit
#
# Runs the project's test command before allowing a commit.
# Blocks the commit if tests fail.
#
# No-op when TEST_COMMAND is still a placeholder (template not configured)
# or when PROJECT_CONTEXT.md doesn't exist.
#
# Buggy code was the #1 friction category (10 occurrences) in Insights report.
# This hook prevents shipping code that breaks existing tests.

TOOL_INPUT=$(cat)
# Hook stdin nests tool args under .tool_input; keep top-level fallback for older harnesses.
REPO_PATH=$(node -e "const j=JSON.parse(process.argv[1]); console.log((j.tool_input&&j.tool_input.repo_path)||j.repo_path||'')" "$TOOL_INPUT" 2>/dev/null)

if [ -z "$REPO_PATH" ]; then
  REPO_PATH=$(pwd)
fi

# Read test command from PROJECT_CONTEXT.md. Tolerates: leading "- " / "* " list
# markers, the "**Test Command**:" label style (java/python variants), and
# surrounding backticks — several variants write commands as `cmd`.
TEST_CMD=$(grep -E '^[-*[:space:]]*\*\*Test( Command)?\*\*:' "$REPO_PATH/PROJECT_CONTEXT.md" 2>/dev/null | sed 's/.*\*\*Test\( Command\)\?\*\*:[[:space:]]*//;s/[[:space:]]*$//;s/^`//;s/`$//' | head -1)

# No-op: no PROJECT_CONTEXT.md or no Test command configured
if [ -z "$TEST_CMD" ]; then
  exit 0
fi

# No-op: placeholder not yet filled in
case "$TEST_CMD" in
  *\{\{*\}\}*) exit 0 ;;
esac

echo "PRE-COMMIT: Running tests ($TEST_CMD)..." >&2
cd "$REPO_PATH" || exit 1

if eval "$TEST_CMD" > /dev/null 2>&1; then
  echo "PRE-COMMIT: All tests passed." >&2
  exit 0
else
  echo "BLOCKED: Tests failed. Fix test failures before committing." >&2
  exit 2
fi
