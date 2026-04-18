#!/usr/bin/env bash
# PreToolUse hook: block push to main/master
# Matcher: mcp__git-tools__git_push
#
# Prevents direct pushes to main/master branches.
# Forces feature branch + PR workflow.

TOOL_INPUT=$(cat)
REPO_PATH=$(node -e "console.log(JSON.parse(process.argv[1]).repo_path||'')" "$TOOL_INPUT" 2>/dev/null)
BRANCH=$(node -e "console.log(JSON.parse(process.argv[1]).branch||'')" "$TOOL_INPUT" 2>/dev/null)

# Resolve implicit branch when not specified
if [ -z "$BRANCH" ] && [ -n "$REPO_PATH" ]; then
  BRANCH=$(git -C "$REPO_PATH" branch --show-current 2>/dev/null)
fi

if [ "$BRANCH" = "main" ] || [ "$BRANCH" = "master" ]; then
  echo "BLOCKED: Direct push to $BRANCH is not allowed. Use a feature branch and create a PR." >&2
  exit 2
fi

exit 0