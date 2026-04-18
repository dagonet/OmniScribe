#!/usr/bin/env bash
# PreToolUse hook: require tier + two-challenge + team-declaration + freshness
# in a plan file before spawning coder agents.
#
# Matcher: Agent
# Applies only to coder variants (coder, dotnet-coder, java-coder,
# python-coder, rust-coder). All other agent types pass through.
#
# A plan file passes when it contains:
#   1. 'Tier: T1'-'T4' declaration (supports '**Tier:** Tn' markdown bold)
#   2. Both 'Challenge 1' AND 'Challenge 2' literal anchors (T3/T4 only)
#   3. 'Team:' line matching the declared tier:
#        T1/T2: any Team line, or none
#        T3:    must include coder, code-reviewer, tester
#        T4:    must include architect, coder, code-reviewer, tester
#   4. mtime within the last 14 days (stale plans rejected)
#
# T1 and T2 plans are exempt from the two-pass challenge (see
# AGENT_TEAM.md Plan Challenge Protocol). They need only tier declaration
# and freshness.
#
# OR semantics: at least one plan file in docs/plans/ or $HOME/.claude/plans/
# must pass all four checks. If none pass, the hook prints a consolidated
# diagnostic and exits 2.
#
# Enforcement model and spoofability caveats: docs/hook-enforcement-ideas.md
# entry #7 and the "Grep-based enforcement: strengths and limits" section.

MAX_STALE_DAYS=14

TOOL_INPUT=$(cat)
SUBAGENT_TYPE=$(node -e "console.log(JSON.parse(process.argv[1]).subagent_type||'')" "$TOOL_INPUT" 2>/dev/null || echo '')

case "$SUBAGENT_TYPE" in
  coder|dotnet-coder|java-coder|python-coder|rust-coder) ;;
  *) exit 0 ;;
esac

get_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0
}

# validate_plan <file>
# Prints a single-line diagnostic and returns 1 on failure; returns 0 on pass.
validate_plan() {
  local file="$1"
  local now mt age_days tier team_line required member

  now=$(date +%s)
  mt=$(get_mtime "$file")
  age_days=$(( (now - mt) / 86400 ))
  if [ "$age_days" -gt "$MAX_STALE_DAYS" ]; then
    echo "  $file: stale ($age_days days old, limit $MAX_STALE_DAYS)"
    return 1
  fi

  tier=$(grep -E '^\*?\*?[Tt]ier:' "$file" 2>/dev/null | grep -oE 'T[1-4]' | head -1)
  if [ -z "$tier" ]; then
    echo "  $file: no 'Tier: T1'-'T4' declaration"
    return 1
  fi

  case "$tier" in
    T1|T2)
      return 0
      ;;
    T3|T4)
      if ! grep -q 'Challenge 1' "$file" 2>/dev/null; then
        echo "  $file ($tier): missing 'Challenge 1' literal (two-pass challenge required for T3+)"
        return 1
      fi
      if ! grep -q 'Challenge 2' "$file" 2>/dev/null; then
        echo "  $file ($tier): missing 'Challenge 2' literal (two-pass challenge required for T3+)"
        return 1
      fi
      team_line=$(grep -E '^\*?\*?[Tt]eam:' "$file" 2>/dev/null | head -1)
      if [ -z "$team_line" ]; then
        echo "  $file ($tier): no 'Team:' declaration"
        return 1
      fi
      if [ "$tier" = "T3" ]; then
        required="coder code-reviewer tester"
      else
        required="architect coder code-reviewer tester"
      fi
      for member in $required; do
        if ! echo "$team_line" | grep -q "$member"; then
          echo "  $file ($tier): Team line missing member '$member'"
          return 1
        fi
      done
      return 0
      ;;
  esac
  return 1
}

diagnostics=""
found_any=0

for dir in "docs/plans" "$HOME/.claude/plans"; do
  [ -d "$dir" ] || continue
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    found_any=1
    if msg=$(validate_plan "$f"); then
      exit 0
    else
      diagnostics="${diagnostics}${msg}"$'\n'
    fi
  done < <(find "$dir" -maxdepth 1 -type f -name '*.md' 2>/dev/null)
done

if [ "$found_any" -eq 0 ]; then
  echo "BLOCKED: No plan files found in docs/plans/ or \$HOME/.claude/plans/. Create a plan with a Tier declaration (plus two-pass challenge for T3/T4) before spawning coder agents." >&2
  exit 2
fi

{
  echo "BLOCKED: No plan passed tier-before-coder checks. Each plan must satisfy:"
  echo "  1. 'Tier: T1'-'T4' declaration"
  echo "  2. Both 'Challenge 1' and 'Challenge 2' literals (T3/T4 only)"
  echo "  3. 'Team:' line matching tier (T3: coder, code-reviewer, tester; T4: + architect)"
  echo "  4. mtime within last $MAX_STALE_DAYS days"
  echo "Plan failures:"
  printf "%s" "$diagnostics"
} >&2
exit 2