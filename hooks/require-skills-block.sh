#!/usr/bin/env bash
# PreToolUse hook: require '## Required Skills' block in spawn prompts for
# bound subagent_types.
#
# Matcher: Task
#
# Enforces the AGENT_TEAM.md Spawn-Prompt Binding Table. When the PO spawns
# a Task whose subagent_type is in the binding table, the prompt body MUST
# contain a literal '## Required Skills' line. Subagent types not in the
# table pass through. The 'code-reviewer' and 'doc-generator' types have
# no required skills and also pass through.
#
# DRIFT WARNING: the case statement below duplicates the binding table in
# templates/general/AGENT_TEAM.md §Superpowers Skills Integration. The
# scripts/verify-template-consistency.sh script diffs the two and fails
# CI if they diverge — keep them in sync.
#
# Reference: docs/plans/2026-04-12-wire-superpowers-skills.md (Chunk B,
# §Architecture). Mirrors the JSON-via-`node -e` parsing pattern used by
# tier-before-coder.sh and no-push-main.sh.

TOOL_INPUT=$(cat)
SUBAGENT_TYPE=$(node -e "console.log(JSON.parse(process.argv[1]).subagent_type||'')" "$TOOL_INPUT" 2>/dev/null || echo '')
PROMPT=$(node -e "console.log(JSON.parse(process.argv[1]).prompt||'')" "$TOOL_INPUT" 2>/dev/null || echo '')

case "$SUBAGENT_TYPE" in
  coder|dotnet-coder|rust-coder|java-coder|python-coder)
    REQUIRED="superpowers:test-driven-development superpowers:verification-before-completion superpowers:receiving-code-review"
    ;;
  tester)
    REQUIRED="superpowers:systematic-debugging superpowers:verification-before-completion"
    ;;
  test-writer)
    REQUIRED="superpowers:test-driven-development"
    ;;
  architect)
    REQUIRED="superpowers:writing-plans"
    ;;
  requirements-engineer)
    REQUIRED="superpowers:brainstorming"
    ;;
  code-reviewer|doc-generator)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac

if printf '%s\n' "$PROMPT" | grep -qE '^## Required Skills$'; then
  exit 0
fi

{
  echo "BLOCKED: Task spawn for subagent_type '$SUBAGENT_TYPE' is missing a '## Required Skills' block in the prompt body."
  echo
  echo "Per AGENT_TEAM.md Spawn-Prompt Binding Table, this subagent_type must invoke:"
  for skill in $REQUIRED; do
    echo "  - $skill"
  done
  echo
  echo "Add this block to the prompt body before spawning:"
  echo
  echo "## Required Skills"
  echo "Invoke these via the Skill tool before beginning task work:"
  for skill in $REQUIRED; do
    echo "- $skill"
  done
} >&2
exit 2