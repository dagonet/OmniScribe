#!/usr/bin/env bash
# SubagentStop hook: contract stop-gate for coder/reviewer agents.
# Matcher: coder|dotnet-coder|rust-coder|java-coder|python-coder|code-reviewer
#
# Blocks (exit 2) an agent from ending WITHOUT its required deliverable:
#   - coder types:    final message must contain "## Gate Results" AND "## Spec Compliance"
#   - code-reviewer:  final message must BE the single word "clean" (strict equality)
#                     or contain a severity-tagged findings list ("**Severity**")
# The stderr is fed back to the agent, which continues and produces the report.
#
# Loop guard: a marker file bounds enforcement to EXACTLY ONE forced continuation
# per session+agent (SubagentStop has no documented stop_hook_active field, so we
# keep our own state). On the second non-compliant stop the agent is let through
# and a non-blocking CONTRACT-ENFORCER stderr line tells the PO to treat the
# report as incomplete.
#
# Deliberately FAIL-OPEN when broken (transcript missing, node absent, fields
# absent, marker dir unwritable): a broken enforcer must never trap an agent.
# Do NOT wrap this hook's registration in the exit-127 fail-closed wrapper used
# for PreToolUse guards — a missing enforcer would otherwise block stops forever.

INPUT=$(cat)

AGENT_TYPE=$(node -e "const j=JSON.parse(process.argv[1]);console.log(j.agent_type||'')" "$INPUT" 2>/dev/null || echo '')
TRANSCRIPT=$(node -e "const j=JSON.parse(process.argv[1]);console.log(j.transcript_path||'')" "$INPUT" 2>/dev/null || echo '')
AGENT_ID=$(node -e "const j=JSON.parse(process.argv[1]);console.log(j.agent_id||'')" "$INPUT" 2>/dev/null || echo '')
SESSION_ID=$(node -e "const j=JSON.parse(process.argv[1]);console.log(j.session_id||'')" "$INPUT" 2>/dev/null || echo '')

# Fail-open: not enough information to enforce.
if [ -z "$AGENT_TYPE" ] || [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  exit 0
fi

# Last assistant entry's text blocks (empty when the final entry is tool_use-only —
# that counts as non-compliant: the agent ended without a report).
LAST_TEXT=$(tail -c 300000 "$TRANSCRIPT" | node -e '
let d="";
process.stdin.on("data",c=>d+=c);
process.stdin.on("end",()=>{
  const lines=d.split("\n").filter(Boolean);
  let txt="";
  for (const l of lines){
    let j; try { j=JSON.parse(l); } catch(e) { continue; } // first line may be cut by tail
    if (j.type==="assistant" && j.message && Array.isArray(j.message.content)){
      txt=j.message.content.filter(b=>b&&b.type==="text").map(b=>b.text||"").join("\n");
    }
  }
  process.stdout.write(txt);
});' 2>/dev/null)

# Verdict per agent type.
ok=0
missing=""
case "$AGENT_TYPE" in
  code-reviewer)
    trimmed=$(printf '%s' "$LAST_TEXT" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ "$trimmed" = "clean" ] || printf '%s' "$LAST_TEXT" | grep -q '\*\*Severity\*\*'; then
      ok=1
    else
      missing="a severity-tagged findings list (each finding with **Severity** and file:line) or the single word 'clean'"
    fi
    ;;
  *)
    gaps=""
    printf '%s' "$LAST_TEXT" | grep -q '## Gate Results'     || gaps="'## Gate Results'"
    printf '%s' "$LAST_TEXT" | grep -q '## Spec Compliance'  || gaps="${gaps:+$gaps and }'## Spec Compliance'"
    if [ -z "$gaps" ]; then
      ok=1
    else
      missing="the required section(s) $gaps"
    fi
    ;;
esac

TMPBASE="${TMPDIR:-${TMP:-${TEMP:-/tmp}}}"
MARKER="$TMPBASE/.contract-prod-${SESSION_ID}-${AGENT_ID}"

if [ "$ok" = "1" ]; then
  rm -f "$MARKER" 2>/dev/null
  exit 0
fi

# Second non-compliant stop: let it through, but signal the PO.
if [ -f "$MARKER" ]; then
  rm -f "$MARKER" 2>/dev/null
  echo "CONTRACT-ENFORCER: $AGENT_TYPE/$AGENT_ID ended without $missing after one prod — treat the report as incomplete and re-dispatch per the stall runbook." >&2
  exit 0
fi

# First non-compliant stop: record the prod, block the stop, tell the agent exactly what to do.
if ! touch "$MARKER" 2>/dev/null; then
  # Cannot persist the loop guard — fail open rather than risk an unbounded block loop.
  echo "CONTRACT-ENFORCER: marker dir unwritable ($TMPBASE); letting $AGENT_TYPE/$AGENT_ID stop unenforced." >&2
  exit 0
fi

if [ "$AGENT_TYPE" = "code-reviewer" ]; then
  echo "CONTRACT VIOLATION: your final message must be either a severity-tagged findings list (each finding: **Severity**: critical|warning|suggestion + file:line locator) or the single word: clean. Post your review result now — do not end without it." >&2
else
  echo "CONTRACT VIOLATION: your final report is missing $missing. Produce it now: run 'bash hooks/run-gate.sh' and paste the verbatim tail under '## Gate Results' (or the Build/Test/Format/Lint outputs if no Gate is configured), then echo every numbered spec item under '## Spec Compliance' as DONE or DEVIATED: <reason>." >&2
fi
exit 2
