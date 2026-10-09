#!/usr/bin/env bash
# PreToolUse hook: require '## Required Skills' block in spawn prompts for
# bound subagent_types.
#
# Matcher: Agent   (was `Task`. The header said Task until v3.0.0 while
#                   settings.json registered Agent, and that drift is the trail
#                   to a hook that had never fired — see the field-path note.)
#
# Enforces `AGENT_TEAM.md` §Spawn-Prompt Binding Table. When the PO spawns
# a Task whose subagent_type is in the binding table, the prompt body MUST
# contain a literal '## Required Skills' line. Subagent types not in the
# table pass through. The 'code-reviewer' type has no required skills and
# also passes through.
#
# DRIFT WARNING: the case statement below duplicates the binding table in
# templates/general/AGENT_TEAM.md §Superpowers Skills Integration. The
# scripts/verify-template-consistency.sh script diffs the two and fails
# CI if they diverge — keep them in sync.
#
# Reference: docs/plans/2026-04-12-wire-superpowers-skills.md (Chunk B,
# §Architecture). Reads its payload through hooks/lib/json.sh, the shared
# node/python3/jq reader the git gates use.

trap '[ "$?" = 127 ] && exit 2' EXIT   # v4.4.0 C2: the old registration wrapper's 127->2, now in-hook (exec/source forms cannot wrap)

# ⚠ NAMED `HOOK_PAYLOAD`, NOT `TOOL_INPUT`, AND THE RENAME IS THE POINT.
# Until v3.0.0 this held the WHOLE stdin document while being called
# `TOOL_INPUT`, and the string `tool_input` appeared nowhere in the file. That
# is why the dead-hook defect below survived review: `json_get "$TOOL_INPUT"
# subagent_type` reads perfectly as "get subagent_type from the tool input", so
# an auditor confirms the intent and moves on. The variable was named for an
# extraction that was never performed.
HOOK_PAYLOAD=$(cat)

# v2.2.0: fields are read through hooks/lib/json.sh (node, python3 or jq).
# Fail-open with one WARN when none of the three is on PATH.
jlib="$(dirname "$0")/lib/json.sh"
[ -f "$jlib" ] || {
  echo "WARN: require-skills-block: hooks/lib/json.sh missing — enforcement inactive" >&2
  exit 0
}
. "$jlib"
# v4.4.0 C3: one parser run for all three fields. rc 2 = no parser (WARN, exit 0, as json_have
# was); rc 1 = invalid payload keeps the old behaviour of this hook: the fields read empty and
# the shape witness below refuses it (it never called json_valid).
json_fields "$HOOK_PAYLOAD" tool_name tool_input.prompt tool_input.subagent_type
[ "$?" = 2 ] && { json_warn_no_parser require-skills-block "$(json_session "$HOOK_PAYLOAD")"; exit 0; }

# ⚠ THE FIELDS ARE NESTED UNDER `tool_input`, AND READING THEM AT THE TOP LEVEL
# MEANT THIS HOOK HAD NEVER FIRED (v3.0.0).
#
# Measured two ways. A payload in the harness's real shape exited 0 for a BOUND
# subagent_type with no `## Required Skills` block; the same payload with the
# fields flattened exited 2. And on the real harness, a bound `architect` spawn
# with no skills block LAUNCHED. So `SUBAGENT_TYPE` was always empty, every
# spawn fell to the `*)` default arm, and the hook exited 0 on every spawn ever
# made.
#
# Every other hook in this repo already reads nested — `hooks/lib/git-cmd.sh`
# takes `tool_input.command`, `hooks/enforce-delegation.sh` takes
# `j.tool_input`. This one was the only top-level reader, and the trail is the
# stale `# Matcher: Task` header above: the field paths were written against the
# old tool's payload and the matcher was renamed to `Agent` without them.
#
# WHY THE SUITE DID NOT CATCH IT: `mkspawn()` in scripts/test-hooks.sh builds a
# FLAT payload, so every existing fixture exercised a shape the harness does not
# send. The nested-shape fixtures added in v3.0.0 are the control that would
# have.
#
# ⚠ WHY THIS IS A v3.0.0 BLOCKER AND NOT A FAST FOLLOW: in the five-binding-form
# taxonomy this hook's `case` arms are form 1 — FAIL OPEN, SILENT. v3.0.0
# consolidates 13 agent names to 10. The mechanism meant to catch a mis-bound
# name after that consolidation is the mechanism that has never run, so the
# rename would ship and the detector would stay silent for the same reason it
# has always been silent.
#
# WHY THE DESCENT IS HERE AND NOT AT THE `$(cat)` ABOVE. Extracting the whole
# `tool_input` OBJECT once, then reading scalars out of it, would be the tidier
# shape — but `hooks/lib/json.sh` cannot do it. MEASURED against the shipped
# reader (node parser):
#
#   json_get "$P" tool_input                 -> []            <- empty
#   json_get "$P" tool_input.subagent_type   -> [architect]
#
# `json_get` returns SCALARS; a sub-object comes back as the empty string, which
# would make the fallback below fire on every payload and restore the exact
# defect. So the descent is expressed as a dotted PATH at the point of use,
# which the reader does support, and the variable above is renamed so it no
# longer claims a descent that never happened.
#
# v4.0.2 (item 14): the top-level fallback that used to live here is DELETED.
# It was read "to be safe" while the live Agent payload could not be observed
# from inside the suite; re-measured from this session's own transcript
# (2026-09-17, client 2.1.274 / 2.1.220) the fallback shape has never once been
# sent by any Claude Code client — it is the shape THIS TOOLKIT's own hook
# wrongly read before toolkit v3.0.0 ($.subagent_type at the top level).
# Reading both shapes did not make the hook safer; it made a malformed or
# unrecognised payload indistinguishable from a legitimate empty field, which
# is exactly the fail-open path this item closes. See the shape witness below.
#
# ⚠ DO NOT "FIX" hooks/enforce-agent-contract.sh BY ANALOGY. It reads
# `agent_type`, `agent_id` and `agent_transcript_path` off the top level with no
# `tool_input` descent, which looks like the same defect and is not: it is a
# SubagentStop hook, and those fields ARE top-level in that event. Changing it
# would break a working hook while fixing a dead one.

# v4.0.2 (item 14): tool_name guard. The matcher scopes this hook to Agent
# today, but a widened matcher must not make it fail closed on a call it was
# never meant to see -- an agent-ADJACENT tool such as SendMessage carries
# `to`/`message` and no `prompt` at all. A NAMED, DIFFERENT tool (tool_name
# present and not Agent/Task) is untouched. tool_name unreadable/absent is
# NOT treated as "some other tool" -- that is the shape anomaly this item
# exists to catch (the harness never omits tool_name), so it falls through to
# the shape witness below and is refused there.
TOOL_NAME=${JF[0]:-}
case "$TOOL_NAME" in
  Agent|Task|"") ;;
  *) exit 0 ;;
esac

# v4.0.2 (item 14): shape witness. `tool_input.prompt` is present on every
# real Agent call (subagent_type is legitimately optional -- its absence alone
# is not a shape change). Empty/absent means the payload is not the shape this
# hook reads; refuse it fail-closed rather than fall through with an empty
# SUBAGENT_TYPE the way the dead top-level fallback used to.
PROMPT=${JF[1]:-}
if [ -z "$PROMPT" ]; then
  echo "BLOCKED: Agent payload carries no tool_input.prompt -- the shape this hook reads is not what arrived; refusing (fail closed). Keys present (any depth): $(printf '%s' "$HOOK_PAYLOAD" | grep -oE '"[a-zA-Z_]+":' | tr -d '":' | tr '\n' ' ')" >&2
  exit 2
fi
SUBAGENT_TYPE=${JF[2]:-}

case "$SUBAGENT_TYPE" in
  # Any language coder, including ones a project adds itself (cpp-coder, …).
  # An enumeration the template owns silently unbinds project coders on sync.
  coder|*-coder)
    REQUIRED="karpathy-guidelines superpowers:test-driven-development superpowers:verification-before-completion superpowers:receiving-code-review"
    ;;
  # v3.0.0 (item B2): `tester` ABSORBED `test-writer` and gained its skill.
  # ABSORB, NEVER RENAME — the survivor keeps the existing name, so a stale
  # `tester` reference in a consumer's keep-mine prose still resolves. Had the
  # merged agent taken a new name, every consumer's prose would have gone stale
  # at once and silently.
  tester)
    REQUIRED="superpowers:systematic-debugging superpowers:verification-before-completion superpowers:test-driven-development"
    ;;
  # v3.0.0 (item B2): `architect` ABSORBED `requirements-engineer` and gained
  # `brainstorming` with it. This deliberately REVERSES the earlier R3 decision
  # that dropped `brainstorming` from the architect row — R3 was correct while a
  # separate agent owned requirements exploration, and is wrong now that the
  # same agent does both. Check 9 asserts the new intent and records the
  # reversal, rather than the old absence quietly outliving its reason.
  architect)
    REQUIRED="superpowers:writing-plans superpowers:brainstorming"
    ;;
  # DELIBERATELY EXEMPT — v2.4.0 (item A5) MADE THIS SET EXPLICIT, and the
  # names are the whole point of the change. Until now `ops` and `Explore`
  # reached the `*)` arm below, which is BYTE-IDENTICAL IN EFFECT to this one:
  # an agent exempted on purpose and an agent whose name silently fell out of
  # the enumeration produced the same `exit 0`, with no signal at runtime or
  # afterwards that distinguished them. A consumer could not tell whether their
  # own `game-tester` was unbound deliberately or by accident.
  #
  # THE OBVIOUS FIX — make `*)` warn — IS UNAVAILABLE, and this programme has
  # already paid for the lesson: a hook that exits 0 has no reliable channel to
  # the transcript, and hook stderr was measured not to reach the lead. A
  # warning nobody receives is the same silence with more code, and it reads as
  # fixed. So the intent is written down HERE, as arm labels, and
  # scripts/verify-template-consistency.sh check 29 asserts statically — in the
  # gate, where output demonstrably reaches someone — that every SHIPPED agent
  # name matches a binding arm or appears on this list. Deleting or renaming an
  # agent without updating this file is red before the release ships.
  #
  # ADDING A NAME HERE IS A DECISION, NOT A TIDY-UP: it says this agent needs
  # no skills block. Do not add a name here to silence check 29 — the check
  # exists to make that decision visible.
  #
  # v3.0.0 (item B2): `doc-generator` LEFT THIS ARM BY BEING ABSORBED INTO
  # `coder`, not by falling out of it — and the difference is the whole reason
  # this list exists. Docs are now a `coder` spawn, which IS bound, so doc work
  # now carries the coder skills. Check 29 arm A is what makes the distinction
  # mechanical: had the agent files been deleted without editing this arm, the
  # gate would be red before the release shipped.
  ops|Explore|code-reviewer)
    exit 0
    ;;
  # The genuinely UNKNOWN type: a project's own agent (a consumer's
  # `game-tester`, a `cpp-coder` they added). It must pass — a template
  # enumeration has no business blocking an agent it never shipped, and check
  # 29 is scoped to the toolkit's own agent set for exactly that reason. A gate
  # that goes red on a consumer's own file is the cries-wolf failure this
  # release exists to reduce.
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
  # This message names a HEADING AT RUNTIME. A shrink of AGENT_TEAM.md can
  # otherwise leave a live error pointing a blocked user at a section that no
  # longer exists — inside the very hook that blocked them. Written in the
  # `§Heading` form so check 30 resolves it against the live headings and goes
  # red if the section is ever renamed or cut.
  echo "Per \`AGENT_TEAM.md\` §Spawn-Prompt Binding Table, this subagent_type must invoke:"
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
