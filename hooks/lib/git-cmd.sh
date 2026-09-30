#!/usr/bin/env bash
# hooks/lib/git-cmd.sh
#
# Shared command parsing for the v2.0 git-native PreToolUse gates
# (pre-commit-test.sh, no-push-main.sh, gate-before-merge.sh).
#
# v1.x keyed those gates on MCP tool names because a blanket hook banned Bash git
# outright. v2.0 allows the native git/gh CLI, so the gates parse
# tool_input.command instead.
#
# Design notes:
#   - Matching is UNANCHORED inside each segment and quotes are stripped, so
#     wrapper payloads (bash -c "...", pwsh -Command "...") are covered. A false
#     positive (echo "git push origin main") is accepted: these gates fail closed.
#   - `<cwd>/.claude/git-guard-off` is the escape hatch for all three gates.
#   - v2.2.0: the payload is read through hooks/lib/json.sh (node, python3 or
#     jq). With NO parser on PATH the gates fail CLOSED — see gc_read_stdin.
#   - v2.2.1: three more ways a gate could not determine the answer, all of
#     which used to resolve to "allow" and now resolve to "refuse":
#     an unparseable payload (gc_read_stdin), a parser that is present but
#     broken (json.sh's json_probe_ok), and an unreplaced `{{...}}`
#     config value (gc_is_placeholder).
#
# WHY THESE GATES SCAN THE WHOLE STRING, and why enforce-delegation.sh does the
# opposite: these three are fail-CLOSED security gates, so an unanchored match
# is correct — `bash -c "git push origin main"` must not evade them, and a false
# positive on `echo "git push origin main"` costs one retry. enforce-delegation
# is fail-OPEN workflow policy, where a false DENY costs real work and no safety
# argument justifies it, so ITS patterns are anchored to command position. Do
# not "fix" these three the same way; the polarity is the whole difference.
#
# EXIT-CODE CONVENTIONS for the gates and the gate runner (v2.2.5). All four
# meanings in one place, because the fourth only makes sense against the others:
#
#     0    pass / not applicable — the tool call proceeds. NOTE it is OVERLOADED
#          at the success end and deliberately so: run-gate.sh also exits 0 when
#          the gate DID NOT RUN (no Gate field, no Gate command matched, or the
#          value is still a {{...}} placeholder), and pre-commit-test.sh cannot
#          tell those from "ran and passed". Documented as intentional, but know
#          the shape: v2.2.4's BOM bug is exactly the mechanism that makes it
#          live — a `**Gate**:` key that stops matching falls to the no-command
#          arm, exits 0, and every commit sails through looking clean. Not in
#          scope for v2.2.5; stated here so the next person adding a code sees
#          the whole picture rather than half of it.
#     2    BLOCK. Retrying after fixing the reported failure can succeed.
#    78    BLOCK, TERMINAL — retrying cannot change the outcome. `GC_TERMINAL_RC`
#          below.
#
#          78 MEANS TERMINAL. THE sysexits NAME IS A COINCIDENCE, NOT A REASON.
#          An earlier draft justified the number as EX_CONFIG ("configuration
#          error"). That argument is withdrawn twice over. First, the class is
#          wider than configuration: the self-reference guard IS a config error,
#          but "not inside a git repository" and a consumer's missing-dependency
#          preflight are ENVIRONMENT errors. All three are terminal — retrying
#          changes none of them — and only one is a config error, so anyone
#          checking the number against sysexits.h and concluding two codes are
#          needed would re-fragment the class this exists to unify. Second, a
#          DOCUMENTED code is MORE likely to collide, not less, because other
#          sysexits-aware programs emit 78 for its documented meaning. Which is
#          the point below.
#
#          THE CLAMP IS WHAT MAKES 78 SAFE, AND THE NUMBER NEVER WAS. A hook
#          NEVER forwards a child's exit code. run-gate.sh runs an arbitrary
#          consumer gate command; if that command exits 78 for its own reasons,
#          forwarding it would hand a plain test failure the terminal remedy
#          text ("edit your **Gate** value") — INVERTED advice, strictly worse
#          than the generic retry line it replaced. So run-gate.sh clamps a
#          gate command's 78 to 1. Anything that starts capturing `$?` and
#          exiting it verbatim opens that channel; do not.
#   127    the HOOK script did not run — its file is missing or its interpreter
#          is unreadable, so the harness never got a verdict out of it. This is
#          why every hook registration is 127-wrapped: an un-run gate must not
#          read as a pass.
#
# THAT LAST LINE IS ABOUT HOOKS, AND ONLY ABOUT HOOKS (v2.2.5). Do not carry it
# across to a CHECKER you invoke yourself. `bash -n <file>` returning 127 is
# bash's own verdict *about the file it was handed* — "No such file or
# directory" — so there it is a hard ERROR naming a missing script, never a
# shrug. Reading it as "did not run" would wave through `bash -n
# hooks/deleted-thing.sh`, which is precisely the condition the wrapper above
# exists to catch. The distinction is the direction of the call: 127 arriving
# FROM the harness means our script was not launched; 127 arriving from a child
# we launched ourselves is that child's answer. See the verdict table in
# user-level-reference/skills/sync-template/SKILL.md step 4.
#
# WHY 78 EXISTS (v2.2.5, consumer report). `run-gate.sh`'s RUN_GATE_ACTIVE guard
# fires correctly when a project's **Gate** value is `bash hooks/run-gate.sh`,
# and then the outer layers buried its accurate one-line diagnosis under two
# generic failures that BOTH said "fix the failures and re-run" — advice to
# retry the one thing that cannot succeed. The defect was not the wording: the
# caller had no way to tell a terminal failure from a retryable one. So the
# distinction is carried in the exit code, and every outer handler suppresses
# its retry advice on GC_TERMINAL_RC *structurally* — it never matches on a
# message or names a particular guard, so the next terminal guard inherits the
# behaviour by exiting 78 and nothing else has to change.
#
# A guard that fires correctly and then prints misleading remediation is not
# much better than one that does not fire. Whatever prints a terminal failure
# ends with the SPECIFIC remedy, and prints no generic "re-run" line at all.
#
# THE BRANCH IS THE FIX; THE NUMBER IS ONLY A NAME. run-gate.sh's
# self-reference guard has exited a distinct, non-generic code (2) since v2.1.3
# and NO outer handler ever read it — before v2.2.5 no script in hooks/ captured
# `$?` into a variable or compared an exit code against anything at all. The
# advice was circular not because the code was indistinguishable but because
# nothing looked at it. So the load-bearing half of this change is
# pre-commit-test.sh and gate-before-merge.sh LEARNING TO BRANCH; renaming 2 to
# 78 while the callers still test truthiness would have been the most expensive
# possible no-op. The rename is still right, for the correct reason: 2 is
# already the hooks' own BLOCK code, so the moment anything does branch, an
# ordinary block and a terminal gate would be indistinguishable.
#
# 78 IS AN INTERNAL SIGNAL AND NEVER A HOOK'S OWN EXIT STATUS. The harness
# treats every non-zero, non-2 PreToolUse exit as a NON-BLOCKING error and lets
# the tool call proceed — so a hook that *exits* 78 warns and ALLOWS. run-gate.sh
# may return 78 to its caller because it is a runner, not a hook;
# pre-commit-test.sh and gate-before-merge.sh must still exit 2 when they block
# and use the 78 only to choose which remediation text to print.
#
# Source it from a hook:  . "$(dirname "$0")/lib/git-cmd.sh"

# The standalone hooks/run-gate.sh repeats this literal (it must run with no
# JSON parser on PATH, which sourcing this file forbids);
# scripts/verify-template-consistency.sh asserts the two copies agree.
GC_TERMINAL_RC=78

# GC_GATE_TTL_S (v4.0.1, item 17) -- the single source of truth for how long a
# gate artifact stays acceptable to gate-before-merge.sh (mtime-based
# freshness check). hooks/run-gate.sh derives its prune window from THIS
# constant rather than a second hardcoded number, so "the prune window is
# always longer than the accept window" is structural: run-gate.sh repeats the
# literal (same standalone reason as GC_KEY_PRE/GC_TERMINAL_RC above) and
# scripts/verify-template-consistency.sh asserts the two copies agree.
GC_GATE_TTL_S=3600

# GC_GATE_PRUNE_S (v4.0.3, R4) -- ONE derived expression, defined once, for
# every 24h-from-the-TTL window in this codebase: run-gate.sh's existing
# last-pass.*.json prune, the new pre-commit-test.sh last-precommit*.json
# prune (item 4), and the tree+env TTL extension (item 13). Never three
# independently-typed "* 24" literals -- a change to GC_GATE_TTL_S must move
# all three together, which only happens if there is exactly one expression
# to move. Seconds; a caller needing minutes (`find -mmin`) divides by 60
# itself, same as the pre-existing run-gate.sh line did before this constant
# existed. hooks/run-gate.sh repeats this line (same standalone reason as
# GC_GATE_TTL_S/gc_gate_dir above); scripts/verify-template-consistency.sh
# asserts the two copies agree.
GC_GATE_PRUNE_S=$(( GC_GATE_TTL_S * 24 ))

# Fail CLOSED when the JSON reader is missing: without it GC_CMD would be empty
# and every gate would allow every command.
gc_json_lib="$(dirname "${BASH_SOURCE[0]:-$0}")/json.sh"
if [ ! -f "$gc_json_lib" ]; then
  echo "BLOCKED: $gc_json_lib missing — run /sync-template step 6b (hooks/lib/json.sh)" >&2
  exit 2
fi
. "$gc_json_lib"

GC_JSON=""
GC_TOOL=""
GC_CWD=""
GC_CMD=""

# GC_GIT_PRE — RETIRED in v4.0.1 (item 10). It was a word-boundary-safe regex
# prefix matching ANY sequence of global-option tokens between `git` and the
# subcommand, used by gc_matches_subcommand's deleted fast path (see that
# function's comment block, below) as a first, looser attempt before falling
# back to a positional token walk. Having two paths that could disagree was
# itself the item-10 defect: the fast path's alternation matched `commit`
# stretched past `log --grep`, so `git -C /x log --grep commit` was read as a
# real commit and ran the target repo's Test on a read-only command. The walk
# is now the sole authority for every caller (gate-before-merge, no-push-main,
# pre-commit-test), so this constant has no remaining reader — confirmed via
# `grep -n GC_GIT_PRE hooks/lib/git-cmd.sh hooks/*.sh`, whose only hits left
# are historical narrative in other hooks' comments about the v3.0.3 widening,
# not live code.

# gc_global_options <segment> -> prints ok | refuse:<opt> | env:<VAR>
#
# v3.0.3 item 1 — ARGV PREFIX SHAPE (finding 60). Lived in gate-before-merge.sh
# as a6_global_options until finding 62 showed no-push-main and pre-commit-test
# need the same classification; one definition, three callers.
#
# A gated git command is `[ENV...] git <globals> <subcommand> ...`. Every global
# before the subcommand is classified by ONE question: does it change what the
# command RESOLVES to? -c/--config-env (config), --git-dir/--work-tree (repo),
# --namespace (ref namespace — reads like a display option, moves the refs the
# decision is made from), --exec-path (which binaries run), --no-replace-objects
# (object resolution). Unknown globals are REFUSED: a future flag cannot
# silently join the allow side. -C is allowed because the callers resolve it.
#
# Complete over the config channel, measured (git 2.55.0): `git pull -c x=y`,
# `git merge -c`, `git push -c` all fail with `unknown switch`, so there is no
# after-the-subcommand position to have a gap in.
#
# THE SIX INERT GLOBALS ARE NOT A CONVENIENCE. --no-optional-locks is what VS
# Code and the JetBrains IDEs pass to avoid touching the index lock; refusing
# tooling nobody typed is the shape that gets a guard switched off.
#
# KNOWN SEAM: gc_segments strips quotes, so a `-C "C:/a b"` path word-splits
# here and the token after `-C` is consumed as its value while the remainder
# falls to the `*)` (subcommand) arm, printing `ok`. That is the same verdict
# the pre-v3.0.3 code gave, so this does not widen it; gc_matches_subcommand's
# own fail-closed retry is what covers the quoted-path case for the verdict.
gc_global_options() {
  gcgo_seg="$1"
  # `set -f` BEFORE the unquoted split (v3.0.3, phantom-token audit). Word
  # splitting is wanted here; PATHNAME EXPANSION is not. Without it a `-C`
  # operand containing `*`, `?` or `[` is globbed against the CURRENT DIRECTORY
  # before this function ever sees it — a repo at `…/w[1]` or `…/w*` either
  # vanishes from the token list or multiplies, and the classifier then answers
  # about a different command than the one that was typed. Restored immediately
  # after, because callers do rely on globbing elsewhere.
  set -f
  # shellcheck disable=SC2086
  set -- $gcgo_seg
  set +f
  gcgo_sawgit=0
  while [ $# -gt 0 ]; do
    gcgo_tok="$1"; shift
    if [ "$gcgo_sawgit" -eq 0 ]; then
      case "$gcgo_tok" in
        env) continue ;;
        git) gcgo_sawgit=1; continue ;;
        *=*) case "$gcgo_tok" in GIT_*=*|*_GIT_*=*) printf 'env:%s\n' "${gcgo_tok%%=*}"; return ;; esac; continue ;;
        *)   continue ;;
      esac
    fi
    case "$gcgo_tok" in
      -C)   shift; continue ;;                                    # resolved by the caller
      -C?*) continue ;;
      --no-pager|-P|--paginate|--no-optional-locks|--literal-pathspecs|--no-lazy-fetch) continue ;;
      # `--attr-source` is listed EXPLICITLY (v3.0.3, consumer argument on
      # merit): it changes which tree gitattributes are read from, i.e. what the
      # command resolves to, so it belongs beside --git-dir and --namespace
      # rather than being caught by the unknown-global default below. Naming it
      # also makes the DENY text say the option instead of "unknown".
      -c|-c?*|--config-env|--config-env=*|--git-dir|--git-dir=*|--work-tree|--work-tree=*|--namespace|--namespace=*|--exec-path|--exec-path=*|--attr-source|--attr-source=*|--no-replace-objects)
            printf 'refuse:%s\n' "${gcgo_tok%%=*}"; return ;;
      -*)   printf 'refuse:%s\n' "$gcgo_tok"; return ;;           # unknown global: fail closed
      *)    printf 'ok\n'; return ;;                              # the subcommand
    esac
  done
  printf 'ok\n'
}

# v2.2.4 -- the tolerant prefix EVERY `**Key**:` anchor over PROJECT_CONTEXT.md
# must start with. Read this before writing a new field extractor:
#
#   the server strips the BOM for hashing and the hooks do not for grepping, so
#   the same file is two different files depending on which subsystem is looking
#
# A UTF-8 BOM (ef bb bf) sits at byte 0, i.e. INSIDE line 1, so `^` no longer
# abuts the key and the grep returns nothing -- and "no field found" is the
# fail-OPEN arm in pre-commit-test.sh (warn and allow), not a parse error. This
# is routine on Windows: PowerShell 5.1's `>` and `Out-File` write UTF-8 WITH a
# BOM, so one redirect gives a config file a permanent, invisible one. The
# exposure is only ever line 1, which is why the bug hides until someone moves
# a key to the top of the file.
#
# GC_KEY_PRE also carries the leading list-marker tolerance ("- " / "* "), so
# an anchor is `grep -E "${GC_KEY_PRE}\*\*Gate\*\*:"`. The paired `sed` needs no
# change: its leading `.*` consumes the BOM along with the marker.
GC_BOM=$(printf '\357\273\277')
GC_KEY_PRE="^(${GC_BOM})?[-*[:space:]]*"

# Reads the hook payload from stdin and populates GC_TOOL / GC_CWD / GC_CMD.
#
# With no JSON parser on PATH the gates cannot see the command at all, so they
# fail CLOSED (exit 2) rather than wave every push and merge through. The
# documented escape hatch still wins — but the payload cwd is unreadable then,
# so it is looked for under the process cwd, which is the project directory
# Claude Code runs hooks in.
gc_read_stdin() {
  GC_JSON=$(cat)
  if ! json_have; then
    GC_CWD=$(pwd)
    GC_TOOL=""
    GC_CMD=""
    gc_guard_off && return 0
    echo "BLOCKED: no JSON parser (node, python3 or jq) on PATH — the git gates cannot inspect the command. Install one, or create <cwd>/.claude/git-guard-off to opt out." >&2
    exit 2
  fi
  # v2.2.1: a payload that does not PARSE is not a payload with no command in
  # it. json_get returns "" for both, and the gates read "" as "nothing to
  # inspect, allow" — so malformed JSON, a truncated payload and empty stdin all
  # exited 0 in silence. The parser is present and working here; the INPUT is
  # the problem, so the message is deliberately distinct from the no-parser one:
  # from outside, the two used to be indistinguishable, which is what made the
  # first report of this read as a false alarm.
  if ! json_valid "$GC_JSON"; then
    GC_CWD=$(pwd)
    GC_TOOL=""
    GC_CMD=""
    gc_guard_off && return 0
    echo "BLOCKED: hook payload did not parse — the git gates cannot inspect the command. Create <cwd>/.claude/git-guard-off to opt out." >&2
    exit 2
  fi
  GC_TOOL=$(json_get "$GC_JSON" tool_name)
  GC_CWD=$(json_get "$GC_JSON" cwd)
  if [ -z "$GC_CWD" ] || [ ! -d "$GC_CWD" ]; then
    GC_CWD=$(pwd)
  fi
  case "$GC_TOOL" in
    Bash|PowerShell)
      # ACCEPTED AND DOCUMENTED, not fixed: json_get prints "" for a
      # `tool_input.command` that is an object or an array, which is
      # indistinguishable here from the key being absent — and an absent key IS
      # a legitimate allow (a Bash payload carrying no command must not block
      # every Bash call; fixture: "Bash payload with no command"). Telling the
      # two apart needs a "key present but non-scalar" probe that json.sh does
      # not have, and the case is not reachable from Claude Code, which always
      # sends a string. Revisit if a real payload ever shows otherwise.
      GC_CMD=$(json_get "$GC_JSON" tool_input.command)
      # v4.1.2 spec §1: join backslash-newline continuations ONCE, here, before
      # gc_protect_c_paths and before any reader -- gc_seg_raw, gc_segments,
      # gc_seg_quoted, gc_augmented_cmd's walk and both guards' fast-exit greps
      # all inherit joined text. A continuation between `gh pr` and `merge`
      # fast-exited the merge gate before this line existed (measured).
      # A FAILED join falls back to the RAW text (reviewer, plan round 1): an
      # empty assignment would hit the guards' `[ -n ]` fast exits and allow
      # everything -- v4.1.1's behaviour is the floor, never allow-all.
      if [ -n "$GC_CMD" ]; then
        _gc_j=$(printf '%s' "$GC_CMD" | cmd_join_continuations) && [ -n "$_gc_j" ] && GC_CMD="$_gc_j"
      fi
      ;;
    *)
      GC_CMD=""
      ;;
  esac
  GC_CMD=$(gc_protect_c_paths "$GC_CMD")
}

# gc_cmd_unreadable -- true when this invocation is one the git gates were
# registered for AND the command string could not be read out of the payload.
#
# THE 14th FAIL-OPEN (v2.2.6 round 2). All three gates carried a bare
# `[ -n "$GC_CMD" ] || exit 0`: a fail-OPEN guard on a fail-CLOSED gate. NO
# DEFECT WAS EVER OBSERVED IN THE FIELD — the "intermittently empty GC_CMD"
# once reported here is RETRACTED, along with the timing that appeared to show
# it (a PreToolUse hook completes BEFORE the Bash tool runs the command, so a
# timer inside the command cannot observe it; see
# .superpowers/sdd/sync-feedback/empty-payload-failopen.md). What stands is the
# STATE, found by reading this code: `command` key present, read yields nothing,
# gate exits 0. In a gate, an unreadable input is a block, never a pass. The fix
# is mechanism-agnostic because every sub-cause below warrants the same refusal.
#
# THE POLARITY IS NOT FLIPPED UNCONDITIONALLY, and that is the whole design. An
# unconditional refusal on an empty GC_CMD would block every legitimate Bash
# payload that carries no command at all — a hard block traded for a silent gap,
# strictly worse. Of the three states, two are ALREADY fail-closed and only the
# third is live:
#
#   stdin empty or unreadable   -> gc_read_stdin exits 2 (json_valid treats
#                                  empty stdin as INVALID, deliberately).
#   no JSON parser on PATH      -> gc_read_stdin exits 2.
#   payload parsed, GC_CMD ""   -> HERE. It splits three ways:
#
#     tool is not Bash/PowerShell   -> GC_CMD is "" BY DESIGN. Allow.
#     no `command` key at all       -> a legitimate Bash payload carrying no
#                                      command (see the note in gc_read_stdin
#                                      and the shipped "Bash payload with no
#                                      command" fixtures). Allow.
#     a `command` key IS present and
#     the read still yielded ""     -> the gate cannot do its job on a real
#                                      invocation. REFUSE.
#
# So this is NARROWER than "missing or empty": an ABSENT key still allows,
# because making that refuse would hard-block a documented, fixture-covered
# legitimate case. Only "present but unreadable" refuses.
#
# THE KEY PROBE IS A GREP OVER THE RAW PAYLOAD, not a json.sh call — the same
# reason json_session greps: the state being detected is one where the parser
# has already returned nothing, so asking it again proves nothing. It is also
# backend-invariant, so it answers identically in all three parser
# configurations. A false positive needs an unreadable command AND the literal
# `"command":` in some other field of the same payload, and it fails CLOSED,
# which is the correct direction for these three gates.
#
# GC_TOOL EMPTY IS DELIBERATELY INCLUDED in the matched set. These gates are
# registered on Bash|PowerShell, so an empty tool_name on a live invocation is
# the identical cannot-determine one field over — the read that came back empty
# just happened to be tool_name instead of tool_input.command. Excluding it
# would leave the same fault silent through a neighbouring door.
gc_cmd_unreadable() {
  [ -z "$GC_CMD" ] || return 1
  case "$GC_TOOL" in
    Bash|PowerShell|'') ;;
    *) return 1 ;;
  esac
  printf '%s' "$GC_JSON" | grep -q '"command"[[:space:]]*:'
}

# gc_protect_c_paths <command> -- make quoted `-C` paths survive quote stripping.
#
# gc_segments deletes quote characters, so `git -C "C:/a b" push` would become
# `git -C C:/a b push`: the strict `git -C <token>` shape stops matching AND
# gc_git_c would yield the truncated "C:/a", which gc_resolve rejects, silently
# falling back to the payload cwd -- so the implicit branch check would then
# evaluate the WRONG repo. Spaces inside a quoted -C argument are replaced with
# \001 here (before any stripping) and decoded again in gc_git_c.
#
# v2.2.0: pure sed, so it no longer needs node. Each pass replaces one blank
# inside a quoted `-C` argument; the loop runs until the string stops changing.
# The quotes themselves are left in place — gc_segments strips them anyway.
GC_SOH=$(printf '\001')
gc_protect_c_paths() {
  [ -n "$1" ] || { printf '%s' ""; return 0; }
  gcs=$1
  while :; do
    gcn=$(printf '%s' "$gcs" | sed \
      -e "s/\(-C[[:space:]]\{1,\}\"[^\"]*\)[[:space:]]\([^\"]*\"\)/\1${GC_SOH}\2/" \
      -e "s/\(-C[[:space:]]\{1,\}'[^']*\)[[:space:]]\([^']*'\)/\1${GC_SOH}\2/")
    [ "$gcn" = "$gcs" ] && break
    gcs=$gcn
  done
  printf '%s' "$gcs"
}

# True when the repo has opted out of the git gates.
gc_guard_off() {
  [ -f "$GC_CWD/.claude/git-guard-off" ]
}

# Splits GC_CMD into segments on && || ; | and newlines, with quote characters
# removed so quoted wrapper payloads become plain text in the same segment.
gc_segments() {
  printf '%s\n' "$GC_CMD" | tr -d "\"'" | tr '|;' '\n\n' | sed 's/&&/\n/g'
}

# gc_seg_quoted -- sibling of gc_segments, additive (no caller of gc_segments
# breaks). Prints one 0|1 per line, same order and count as gc_segments' own
# output: 1 when that segment carried a quote character BEFORE stripping.
#
# v3.1. A wrapper payload such as `bash -c "git commit -m x"` collapses to ONE
# segment once quotes are gone, indistinguishable there from an unwrapped
# `git commit -m x` -- a caller that cares whether the commit segment it
# matched came from inside such a wrapper reads this list instead. Built by
# re-splitting the UNSTRIPPED command on the identical delimiters -- removing
# quote characters never changes where && ; | fall, so the segment boundaries
# line up -- and testing each raw segment for a leftover quote character.
#
# A SEPARATE function, not a side-effect global set inside gc_segments: the
# caller captures gc_segments via `segments=$(gc_segments)`, and a variable
# assigned INSIDE a function invoked through command substitution is a
# subshell's own copy -- it vanishes the instant that subshell exits, never
# reaching the caller. Measured red before this split existed as its own
# function. Callers assign the result to a variable named GC_SEG_QUOTED
# themselves, e.g. `GC_SEG_QUOTED=$(gc_seg_quoted)`.
gc_seg_quoted() {
  printf '%s\n' "$GC_CMD" | tr '|;' '\n\n' | sed 's/&&/\n/g' | while IFS= read -r _gcsq_raw; do
    case "$_gcsq_raw" in
      *[\"\']*) printf '1\n' ;;
      *)        printf '0\n' ;;
    esac
  done
}

# gc_seg_raw -- sibling of gc_segments/gc_seg_quoted, additive (no caller of
# either breaks). Prints the RAW segment text, quotes intact, one per line,
# same order and count as gc_segments' own output -- built by re-splitting
# the UNSTRIPPED $GC_CMD on the identical delimiters (removing quote
# characters never changes where && ; | fall, so segment boundaries line up
# -- the same index-alignment argument gc_seg_quoted's own docstring makes at
# :419-429). gc_script_body (v4.1.1, #11) scans THIS form: a wrapped-script
# scan that only ever saw quote-stripped text cannot tell an interpreter word
# from the same word sitting inside somebody else's quoted argument.
gc_seg_raw() {
  printf '%s\n' "$GC_CMD" | tr '|;' '\n\n' | sed 's/&&/\n/g'
}

# gc_script_body <raw_segment> <cwd> -- when a segment runs a SCRIPT FILE
# (`bash|sh|source|. <path> [args]`, a wrapper -- `command`, `env`, `exec`,
# `nohup`, `/usr/bin/env`, `nice -n 10`, `timeout -s KILL 5`, whatever the
# next one is -- optionally in front of it), print the first 16 KB of that
# file so the caller can feed it to the same matcher as extra segments.
# Depth 1 only: a script that invokes another script is a documented residual
# (v4.0.3 item 12). TOCTOU residual: the hook reads the file, allows the
# command, and nothing stops the file changing before bash runs it --
# unfixable at this layer, stated so nobody believes the gate is stronger
# than it is. `[ -f ]`, never `[ -r ]`: a directory is readable.
#
# v4.1.1 (#11). <raw_segment> is gc_seg_raw's form (quotes intact), not
# gc_segments' stripped form -- the caller passes RAW now; this function
# strips quotes itself, per token, as it goes. Precondition established here,
# not documented as someone else's job: no wrapper enumeration (one would
# miss the next wrapper), just a bounded leading-run scan --
#   - the FIRST token whose raw form contains a quote character (`"` or `'`)
#     STOPS the scan with nothing found: an interpreter word is never itself
#     inside quotes, so a quote seen before a match means whatever follows
#     belongs to someone else's argument, not this segment's own head --
#     `git commit -m "run bash x.sh"`, `gh pr merge --body "see bash
#     notes.sh"`, `echo "run bash x.sh"` and `npm run lint -- "bash x.sh"`
#     all stop here, one to three tokens in, well before the "bash" that
#     happens to sit inside their own quoted string.
#   - EVERY token before that (VAR=value, `-i`/`-n 10`/`-s KILL`-shaped
#     flags and their values, bare wrapper words, absolute-path wrappers
#     like `/usr/bin/env`) is skipped -- basename-matched against
#     `bash|sh|source|.` and, on no match, simply passed over.
#   - `bash`/`sh` match at ANY position in the run. `.`/`source` match ONLY
#     at position 1 (the segment's own head): both are shell BUILTINS, not
#     PATH executables, so wrapping them through env/nice/timeout/nohup/
#     command -- all of which execve a real binary -- does not actually
#     invoke them. This is also what keeps `find . -name p.sh -exec bash {}
#     \;`'s own `.` (a find OPERAND, not an interpreter) from being misread
#     as a match before the scan ever reaches the real `bash` later in the
#     same segment -- that fixture still resolves via `bash`, with the
#     placeholder `{}` then failing the file test (a documented residual,
#     not a hole in the quote-stop).
# A quoted INTERPRETER head (`"bash" probe.sh`) is a residual in the other
# direction: the quote-stop fires on the very first token, before it is ever
# basename-compared, so this is not read even though gc_segments' own quote
# stripping would have made $1 literally `bash` under the old $1-anchor code.
gc_script_body() {
  local seg="$1" cwd="$2" tok clean base path="" pos=1
  set -- $seg
  while [ $# -gt 0 ]; do
    tok="$1"
    case "$tok" in *[\"\']*) return 0 ;; esac
    clean=$(printf '%s' "$tok" | tr -d "\"'")
    base=${clean##*/}
    case "$base" in
      bash|sh) break ;;
      .|source) [ "$pos" = 1 ] && break ;;
    esac
    shift
    pos=$((pos + 1))
  done
  [ $# -gt 0 ] || return 0
  shift
  for tok in "$@"; do
    clean=$(printf '%s' "$tok" | tr -d "\"'")
    case "$clean" in -*) continue ;; *) path="$clean"; break ;; esac
  done
  [ -n "$path" ] || return 0
  case "$path" in /*|[A-Za-z]:*) ;; *) path="$cwd/$path" ;; esac
  [ -f "$path" ] || return 0
  # v4.1.2 #8: whole-line comments (first non-blank character `#`) are never
  # commands, so they are stripped BEFORE the verb scan -- and only whole
  # lines: `"${BR#refs/heads/}"` on a code line keeps its `#`. The
  # continuation join runs on this output in gc_augmented_cmd, AFTER this
  # strip (spec §0): bash does not continue a line inside a comment, so
  # join-then-strip would merge `# note \<LF>git push origin main` into the
  # comment and delete the push.
  head -c 16384 "$path" 2>/dev/null | grep -v '^[[:space:]]*#'
}

# gc_augmented_cmd <cwd> -- GC_CMD, plus the body of every script segment
# (gc_script_body, depth 1: a body is never itself re-scanned for further
# script segments) appended after the segment that named it. Walks
# gc_seg_raw's output exactly once (v4.1.1: was gc_segments -- gc_script_body
# now needs the quote-intact form; see its own docstring), over the
# UNMODIFIED $GC_CMD -- so the result is the same text whether a caller feeds
# it straight back into gc_segments/gc_seg_quoted/gc_seg_raw (all three stay
# index-aligned because they read the identical augmented string) or into a
# plain git-token grep (v4.0.3 item 12: the pre-filter in
# gate-before-merge.sh/no-push-main.sh must see the same text the segment
# walk does, or a script's `git merge`/`git push` passes the "no git token"
# fast exit before the walk that would have caught it ever runs).
gc_augmented_cmd() {
  local cwd="$1" out="$GC_CMD" seg body _gc_bj
  while IFS= read -r seg; do
    [ -n "$seg" ] || continue
    body=$(gc_script_body "$seg" "$cwd")
    if [ -n "$body" ]; then
      _gc_bj=$(printf '%s' "$body" | cmd_join_continuations) && [ -n "$_gc_bj" ] && body="$_gc_bj"
    fi
    [ -n "$body" ] && out="$out
$body"
  done <<GC_AUG_SEGS
$(gc_seg_raw)
GC_AUG_SEGS
  printf '%s' "$out"
}

# Prints the `cd <target>` argument of a segment, if the segment is a bare cd.
gc_cd_target() {
  printf '%s\n' "$1" | sed -n 's/^[[:space:]]*cd[[:space:]]\+\([^[:space:]]\+\)[[:space:]]*$/\1/p' | head -1
}

# gc_git_c IS GONE (v3.0.3 defect 1). It required `-C` to sit immediately after
# the literal `git` and took only the first one; see gc_dash_c_list below, which
# replaces it with a walk over the globals.

# gc_classify_c <operand> <base> -- prints one of:
#   1                       literal path (absolute or relative), resolved by
#                           plain `[ -d ]` -- git's own resolution, unaided.
#   2\n<substituted-path>   resolvable by STRING SUBSTITUTION alone (no eval,
#                           no unquoted ~, no $(...), no sh -c: this operand
#                           text is compared against a fixed literal prefix
#                           and, on a match, that prefix is replaced by a
#                           value already sitting in THIS process's own
#                           environment or by <base> -- nothing is executed
#                           to produce it).
#   3                       cannot-determine: the hook cannot know what this
#                           operand resolves to without executing it.
#
# v3.0.3 DEFECT 2 (security fix). The pre-existing arm below (`gc_resolve`,
# byte-identical back through v3.0.0) matched only `/*` and `[A-Za-z]:[/\\]*`
# as "absolute" and treated everything else, including `~/x` and `$HOME/x`,
# as a plain relative path tested against <base> -- which is almost always
# false, so it silently fell back to <base>. That is a STRICT SUPERSET of
# git's own unresolvable set: the real shell expands `~` and `$VAR` before
# git ever sees them, so `git -C ~/protected push` was judged against the
# WRONG repo (the payload cwd) while landing in the REAL one -- a silent
# bypass, live since v3.0.0. v3.0.3's own new multi-`-C` refusal machinery
# (gc_dash_c_unresolved) then went the other way and refused a `~`/`$HOME`
# double-`-C` outright, even though git resolves it fine -- a false refusal,
# and "a denied legitimate command is the guard people switch off".
#
# CLASS 3 KEYS ON THE PRESENCE of a shell metacharacter ($, ~, $(, <(, >(, a
# backtick) -- NOT on whether that character would actually expand. The hook
# payload does not carry which shell ran the command (bash vs PowerShell vs
# cmd), so "would this expand" is unanswerable from here; "does this LOOK
# like something only the shell could resolve" is answerable and is the
# question this asks. Under bash `~` expands (the bypass this closes) and
# refusing it is correct; under PowerShell `~` is literal and git fails on
# its own, so refusing it there is harmless -- refusing something that would
# have failed regardless. Same rule, both directions, no per-shell arm needed.
#
# `%VAR%` (cmd.exe style) is left OUT of class 3 on purpose: bash and
# PowerShell both leave it as a literal string, git fails to chdir into it,
# and that is exactly the documented single-`-C` fallback (class 1).
gc_classify_c() {
  gccc_op="$1"; gccc_base="$2"
  case "$gccc_op" in
    *'$('*|*'`'*|*'<('*|*'>('*) printf '3\n'; return ;;
  esac
  case "$gccc_op" in
    '~')      printf '2\n%s\n' "$(gccc_norm "$HOME")"; return ;;
    '~/'*)    printf '2\n%s/%s\n' "$(gccc_norm "$HOME")" "${gccc_op#\~/}"; return ;;
    '~'*)     printf '3\n'; return ;;  # ~user/... -- not worth string-resolving another user's home
  esac
  case "$gccc_op" in
    '$HOME'|'${HOME}')
      printf '2\n%s\n' "$(gccc_norm "$HOME")"; return ;;
    '$HOME/'*)
      printf '2\n%s/%s\n' "$(gccc_norm "$HOME")" "${gccc_op#\$HOME/}"; return ;;
    '${HOME}/'*)
      printf '2\n%s/%s\n' "$(gccc_norm "$HOME")" "${gccc_op#'${HOME}/'}"; return ;;
    '$USERPROFILE'|'${USERPROFILE}')
      [ -n "$USERPROFILE" ] && { printf '2\n%s\n' "$(gccc_norm "$USERPROFILE")"; return; }
      printf '3\n'; return ;;
    '$USERPROFILE/'*)
      [ -n "$USERPROFILE" ] && { printf '2\n%s/%s\n' "$(gccc_norm "$USERPROFILE")" "${gccc_op#\$USERPROFILE/}"; return; }
      printf '3\n'; return ;;
    '${USERPROFILE}/'*)
      [ -n "$USERPROFILE" ] && { printf '2\n%s/%s\n' "$(gccc_norm "$USERPROFILE")" "${gccc_op#'${USERPROFILE}/'}"; return; }
      printf '3\n'; return ;;
    # $PWD/${PWD} resolve against <base> -- the PAYLOAD's cwd -- and NEVER
    # against this hook process's own $PWD. The hook runs where the harness
    # started it; the command runs in the payload's cwd. Substituting the
    # hook's own $PWD would judge a repo the user never named while reporting
    # a plausible-looking path -- the original bypass, reintroduced, and
    # harder to notice because it no longer falls back visibly.
    '$PWD'|'${PWD}')   printf '2\n%s\n' "$gccc_base"; return ;;
    '$PWD/'*)          printf '2\n%s/%s\n' "$gccc_base" "${gccc_op#\$PWD/}"; return ;;
    '${PWD}/'*)        printf '2\n%s/%s\n' "$gccc_base" "${gccc_op#'${PWD}/'}"; return ;;
  esac
  case "$gccc_op" in
    *'$'*) printf '3\n'; return ;;   # any other $VAR (set, unset, or malformed): cannot-determine
  esac
  printf '1\n'
}

# gccc_norm <value> -- forward-slash form of a HOME/USERPROFILE value so it
# compares against `[ -d ]` the same way this host's other paths do (Git Bash
# `$HOME` is already `/c/...`; `$USERPROFILE` inherited from Windows is
# `C:\...`). Pure string translation, no execution.
gccc_norm() {
  printf '%s' "$1" | tr '\\' '/'
}

# gc_c_resolves <operand> <base> -- the absolute path <operand> resolves to
# under gc_classify_c's rules, or "" when it does not resolve at all.
gc_c_resolves() {
  gccr_out=$(gc_classify_c "$1" "$2")
  gccr_cls=$(printf '%s\n' "$gccr_out" | sed -n 1p)
  case "$gccr_cls" in
    1)
      case "$1" in
        /*|[A-Za-z]:[/\\]*) [ -d "$1" ] && printf '%s\n' "$1" ;;
        *)                  [ -d "$2/$1" ] && printf '%s\n' "$2/$1" ;;
      esac
      ;;
    2)
      gccr_path=$(printf '%s\n' "$gccr_out" | sed -n 2p)
      [ -d "$gccr_path" ] && printf '%s\n' "$gccr_path"
      ;;
    *) ;;
  esac
}

# gc_resolve <base> <path> -- absolute path, or the base when <path> is not a
# dir. Routed through the class-1/class-2 resolver above so `~`, `$HOME`,
# `$USERPROFILE` and `$PWD` (mapped to <base>) resolve the same way git's own
# shell would, instead of falling back to <base> as pure-literal matching did.
# A class-3 (cannot-determine) operand also falls back to <base> HERE -- this
# function only builds the best-effort folded path; REFUSAL for class 3 is a
# separate decision made by gc_dash_c_unresolved below.
gc_resolve() {
  gcr_r=$(gc_c_resolves "$2" "$1")
  if [ -n "$gcr_r" ]; then
    printf '%s\n' "$gcr_r"
  else
    printf '%s\n' "$1"
  fi
}

# gc_dash_c_list <segment> -- every `-C` operand of a segment, one per line, in
# argv order, with the \001 placeholders of gc_protect_c_paths decoded back to
# spaces. Empty when the segment carries no `-C`.
#
# WHY THIS IS A PARSER AND NOT A REGEX (v3.0.3 defect 1, second finding). The
# function it replaces, `gc_git_c`, was
#
#   sed -n 's/.*\bgit[[:space:]]\+-C[[:space:]]\+\([^[:space:]]\+\).*/\1/p' | head -1
#
# which required `-C` to sit IMMEDIATELY after the literal `git` and took only
# the first one. Two live holes, both measured at 0d7806e:
#
#   ORDER       `git -c a=b -C <protected repo> merge feature/y` from an
#               unprotected cwd -> 0. The leading `-c` defeats the regex,
#               gc_git_c returns EMPTY, and gc_repo_for falls back to the
#               payload cwd. From the protected cwd the same command returned 2
#               — by luck, not by judgement.
#   REPETITION  `head -1` takes the FIRST `-C`, so
#               `git -C <feature repo> -C <protected repo> push` resolved to the
#               feature repo and went ungated.
#
# FINDING 63 IS THE MIRROR IMAGE OF THE v3.0.2 ARGV-PREFIX DEFECT: that one was
# blind to unlisted globals AFTER the subcommand position; `gc_git_c` was blind
# to listed globals BEFORE `-C`. Same lib, same shape, opposite direction.
#
# The globals are now WALKED: every token after `git` is consumed until the
# first non-option token, which is the subcommand. Stopping at the subcommand is
# load-bearing in the other direction too — `git commit -C <commit>` reuses a
# commit message and is not a directory change, and `git log -C` is copy
# detection. Value-taking globals swallow their operand so that a value can
# never be mistaken for the subcommand.
gc_dash_c_list() {
  # FAST PATH, and it is the common one. The walk below costs a `sed`, a `tr`
  # and a subshell per call, and this function is reached twice per gated arm
  # (gc_repo_for and gc_dash_c_unresolved) inside the segment loop.
  # (lines kept to preserve line references in the v3.0.3 verification record)
  # Cost of this walk was measured for v3.0.3 — see CHANGELOG v3.0.3 item 10;
  # do not restate numbers here. A literal `-C` test is a builtin `case`,
  # costs nothing, and cannot change the answer: no `-C` in the string means
  # no `-C` operand to find.
  case "$1" in *-C*) ;; *) return 0 ;; esac
  # THE `git` TOKEN IS FOUND POSITIONALLY, NOT BY REGEX (v3.0.3, phantom-token
  # audit). The first draft used
  #
  #     sed -n 's/.*\bgit[[:space:]]\+\(.*\)/\1/p'
  #
  # whose leading `.*` is GREEDY: it anchors on the LAST `git` followed by
  # whitespace anywhere in the segment. An operand whose final component is
  # literally `git` — `git -C /srv/git merge …` — put a `git ` inside the PATH,
  # so the tail became `merge …`, the walk hit a non-option token immediately,
  # and NO `-C` was found. Same layer error as the defect this function was
  # written to fix. The loop below skips tokens until the first one that IS the
  # `git` command (bare, or a path ending in `/git`), and everything after it is
  # argv. Nothing about an operand's CONTENTS can move that boundary.
  gcdl_want=0
  gcdl_seen=0
  printf '%s\n' "$1" | tr ' \t' '\n\n' | while IFS= read -r gcdl_t; do
    [ -n "$gcdl_t" ] || continue
    if [ "$gcdl_seen" -eq 0 ]; then
      case "$gcdl_t" in
        git|*/git|*\\git) gcdl_seen=1 ;;
      esac
      continue
    fi
    case "$gcdl_want" in
      1) gcdl_want=0; continue ;;
      2) printf '%s\n' "$gcdl_t" | tr '\001' ' '; gcdl_want=0; continue ;;
    esac
    case "$gcdl_t" in
      -C)  gcdl_want=2 ;;
      -C*) printf '%s\n' "${gcdl_t#-C}" | tr '\001' ' ' ;;
      --*=*) ;;
      *)
        case "$gcdl_t" in
          -*)
            # shellcheck disable=SC2254
            case "$gcdl_t" in
              -c|--config-env|--git-dir|--work-tree|--namespace|--exec-path|--attr-source|--super-prefix) gcdl_want=1 ;;
              *) ;;
            esac ;;
          *=*) ;;                      # an environment assignment before `git`
          *) break ;;                  # the subcommand
        esac ;;
    esac
  done
}

# gc_repo_for <segment> <base> -- the repo a segment operates on.
#
# REPEATED `-C` IS FOLDED IN ARGV ORDER (v3.0.3, defect 1). MEASURED, git
# 2.55.0 on this host: `-C` is repeatable and CUMULATIVE, each operand relative
# to the one before — `git -C a -C b` chdirs into `a` and then into `b` INSIDE
# it ("fatal: cannot change to 'b'" for a sibling `b`), and `git -C /g/x -C /g/y
# rev-parse` resolves to `/g/y`. So an absolute second operand OVERRIDES and a
# relative second one COMPOSES.
#
# WHY THE FOLD LIVES HERE AND NOT AT A CALL SITE. v3.0.3's first pass put it in
# `a6_repo_for` in hooks/gate-before-merge.sh, on the stated grounds that
# touching this lib would drag the ~90-minute three-parser matrix into the
# release. That reason is void — this file already carries functional change in
# v3.0.3 (the widened GC_GIT_PRE, gc_global_options moved in), so the matrix is
# already mandatory before the tag — and the local copy was wrong in the way a
# local copy is always wrong here: THREE hooks resolve a repo through this
# function and the copy fixed ONE of them, while the OTHER of the two rules
# (order) was in the lib and so was fixed in none.
gc_repo_for() {
  gcrf_list=$(gc_dash_c_list "$1")
  if [ -z "$(printf '%s' "$gcrf_list" | tr -d '[:space:]')" ]; then
    printf '%s\n' "$2"
    return
  fi
  gcrf_base="$2"
  while IFS= read -r gcrf_p; do
    [ -n "$gcrf_p" ] || continue
    gcrf_base=$(gc_resolve "$gcrf_base" "$gcrf_p")
  done <<GC_DASHC
$gcrf_list
GC_DASHC
  printf '%s\n' "$gcrf_base"
}

# gc_dash_c_unresolved <segment> <base> -- empty when nothing needs refusing;
# otherwise two lines: a kind (`cannot-determine` or `unresolved`) and the
# operand to name in the message. Applies to SINGLE and MULTI `-C` alike
# (v3.0.3 defect 2) -- the pre-fix `>= 2` guard below meant a single
# `-C ~/protected` was never even classified, which is exactly how the silent
# bypass reached gc_repo_for unchallenged.
#
# THE RULE, IN PRECEDENCE ORDER:
#
#   0. No `-C` at all -> "" (nothing to refuse; must stay true after removing
#      the old `>= 2` guard, or every -C-free command -- including this file's
#      OWN `git commit -F <file>` -- would be refused).
#   1. ANY operand classifies as class 3 (cannot-determine, see
#      gc_classify_c) -> "cannot-determine\n<that operand>", regardless of
#      how many `-C` there are or whether other operands resolve. This is
#      new, count-independent behaviour: a single unresolvable-by-string
#      operand is exactly the shape the hook must refuse rather than guess.
#   2. Otherwise, single `-C` -> "" always. A class-1 miss keeps the
#      documented exit-0 fallback (git fails on its own); a class-2 hit
#      RESOLVES (closing the bypass) and is judged as the real repo by
#      gc_repo_for, not refused.
#   3. Otherwise, multi `-C`, all class 1/2:
#        a. When the FINAL folded path resolves, it is judged AS GIT WOULD
#           RESOLVE IT — relative composes, absolute overrides. `git -C
#           <protected> -C <unprotected>` is therefore judged as the
#           UNPROTECTED repo and allowed, because that is where git lands.
#        b. Only when the final path does NOT resolve is the strictest
#           RESOLVED candidate judged (gc_resolve keeps the last resolved
#           partial): `git -C <protected> -C nope` is judged as the protected
#           repo. Git will fail on its own anyway, so nothing lands either
#           way.
#        c. Refusal ("unresolved\n<first operand>") only when NO candidate
#           resolves at all -- the cannot-determine-by-resolution case.
#
# The first draft refused any multi-`-C` fold with an unresolvable step. That
# was too blunt: a denied legitimate command is the guard people switch off.
#
# THE ASYMMETRY BETWEEN CLASS-1-MISS AND CLASS-3 ON A SINGLE `-C` IS
# DELIBERATE. `git -C nope merge` (class 1, no such directory) keeps its
# documented exit 0 -- falling back to the cwd and letting git fail; nothing
# about that operand is ambiguous, it is simply absent. `git -C $(cat x)
# merge` (class 3) is refused even alone -- the hook genuinely cannot know
# what that resolves to, which is a different fact about the SAME position.
gc_dash_c_unresolved() {
  gcdu_list=$(gc_dash_c_list "$1")
  [ -n "$(printf '%s' "$gcdu_list" | tr -d '[:space:]')" ] || return 0
  gcdu_base="$2"
  gcdu_first=""
  gcdu_any_resolved=0
  gcdu_class3=""
  while IFS= read -r gcdu_p; do
    [ -n "$gcdu_p" ] || continue
    [ -n "$gcdu_first" ] || gcdu_first=$gcdu_p
    gcdu_cls=$(gc_classify_c "$gcdu_p" "$gcdu_base" | sed -n 1p)
    if [ "$gcdu_cls" = 3 ] && [ -z "$gcdu_class3" ]; then
      gcdu_class3=$gcdu_p
    fi
    gcdu_r=$(gc_c_resolves "$gcdu_p" "$gcdu_base")
    if [ -n "$gcdu_r" ]; then
      gcdu_any_resolved=1
      gcdu_base=$gcdu_r
    else
      gcdu_base=$(gc_resolve "$gcdu_base" "$gcdu_p")
    fi
  done <<GC_DASHU
$gcdu_list
GC_DASHU
  if [ -n "$gcdu_class3" ]; then
    printf 'cannot-determine\n%s\n' "$gcdu_class3"
    return 0
  fi
  gcdu_count=$(printf '%s\n' "$gcdu_list" | grep -c '[^[:space:]]')
  [ "$gcdu_count" -ge 2 ] || return 0
  [ "$gcdu_any_resolved" -eq 0 ] && printf 'unresolved\n%s\n' "$gcdu_first"
  return 0
}

# gc_current_branch <repo>
gc_current_branch() {
  git -C "$1" branch --show-current 2>/dev/null
}

# gc_gate_dir <cwd> -- the gate artifact directory shared by every worktree of
# a repo: <common git dir>/gate. Inside .git, so never a working-tree object
# (v4.0.1, item 17): no gitignore entry needed, cannot be swept into a commit,
# one location for every worktree by construction. `--path-format=absolute`
# (git >= 2.31) is required: the bare `--git-common-dir` prints a RELATIVE
# `.git` from the main checkout and an ABSOLUTE path from a linked worktree --
# two different spellings of the same directory, which is exactly the
# difference that would break a worktree-to-main-checkout handoff. Prints
# forward slashes on Windows (`G:/git/...`); callers must never compare it
# against a backslash spelling. Falls back to <toplevel>/.gate (the pre-4.0.1,
# per-worktree location) on git < 2.31, with a WARN -- a caller writing a
# diagnostic that must never itself block (pre-commit-test.sh's pct_note) is
# responsible for swallowing that WARN, same as every other failure there.
#
# hooks/run-gate.sh repeats this function (same standalone reason as
# GC_KEY_PRE/GC_TERMINAL_RC/GC_GATE_TTL_S above); scripts/verify-template-
# consistency.sh asserts the two copies agree.
#
# v4.0.3 item 8 (R2, reviewer, measured on git 2.55): `gc_gate_dir
# <non-repo target>` used to fail BOTH git calls, print the literal `/.gate`
# (the MSYS root -- `C:\Program Files\Git\.gate` -- outside every repo), warn
# `WARN: git < 2.31` (false on a current git) and let the fallback's own
# `fatal:` leak beside it; `gc_gate_dir ""` resolved against the PROCESS cwd
# and returned the CORRECT directory of the WRONG repo -- a plausible answer
# for the wrong reason. FIX: `[ -n "$1" ] || return 1` FIRST, before any git
# call -- an empty target must never reach `git -C ""`, which git reads as
# "the cwd" and answers successfully. Then resolve the target to its
# toplevel with `--show-toplevel`; only THAT call's failure means "unresolved"
# (return 1, print nothing, no WARN). The `--path-format=absolute
# --git-common-dir` probe runs against the resolved $top, and its OWN
# failure (a toplevel that resolved but is running an old git) is the one
# case that still gets the documented WARN + <toplevel>/.gate fallback.
gc_gate_dir() {
  local top common
  [ -n "$1" ] || return 1
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 1   # unresolved target: nothing
  common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=""
  if [ -n "$common" ]; then printf '%s/gate\n' "$common"; return 0; fi
  echo "WARN: git < 2.31: gate artifacts stay at <toplevel>/.gate (per-worktree)" >&2
  printf '%s/.gate\n' "$top"
}

# gc_sha256 <stdin -> lowercase hex sha256>. Never `sha256sum <path>` -- that
# prints the PATH into the digest input, which would make a fingerprint
# depend on the absolute path of the file being hashed (see gc_gate_env
# below, whose whole point is a fingerprint that agrees across worktrees at
# different absolute paths). Tries sha256sum (Linux, Git Bash), then
# shasum -a 256 (macOS ships this, not sha256sum, by default), then openssl.
# Prints nothing and returns 1 when none is on PATH -- callers must treat
# that as "cannot compute", never as an empty-string hash that would
# spuriously match another "cannot compute" (fail CLOSED, v4.0.3 item 13).
gc_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 | awk '{print $NF}'
  else
    return 1
  fi
}

# gc_gate_env <repo_top> [-v] -- v4.0.3 item 13: the environment fingerprint
# the tree+env TTL extension in gate-before-merge.sh keys on. Concatenates
# labelled contributors the server test suite's outcome can depend on, one
# per line:
#   pyvenv=<sha256 of server/.venv/pyvenv.cfg when a venv is present; else
#           sys:<sha256 of the resolved interpreter's sys.prefix>; "absent"
#           only when no interpreter resolves>
#   dist=<sha256 of the SORTED, newline-joined *.dist-info directory NAMES
#         (basenames only) reported by `site.getsitepackages()` under the
#         INTERPRETER THE GATE RUNS, or "absent" -- v4.1.1 (#15): the venv's
#         own python when server/.venv is present (a present-but-broken venv
#         does not fall back), else python3/python on PATH when it is not.
#         Was site-packages under the venv ONLY, which read "absent" on any
#         consumer with system Python and no in-repo venv -- the one
#         contributor that moves on a dependency change was blind on exactly
#         the repos whose gate is python. A directory read that moves on any
#         install, upgrade or removal, so `pip install -U mcp` changes it>
#   py=<--version of that SAME interpreter, or "absent">
#   node=<node --version if node is on PATH, or "absent">
# Default output: the sha256 hex of that text (this is what "env" in the
# gate artifact stores). With a second argument "-v": the labelled lines
# themselves, for a human or run-gate.sh's own per-contributor storage --
# never re-derived from the aggregate hash, which is one-way by design.
#
# POLARITY (v4.1.1 #15, spec docs/plans/2026-09-21-v4.1.1-design.md §4.3):
# gate-before-merge.sh's tree+env TTL extension VOIDS outright the moment
# EITHER side's env_detail contains an `=absent` contributor -- a
# cannot-determine value must never sit inside a matching aggregate (two
# "absent"s hash equal and would otherwise read as "unchanged"). Enforced in
# gate-before-merge.sh, not here; this function only ever reports what it
# measured, honestly, including "absent".
#
# PATH-INDEPENDENT BY CONSTRUCTION (reviewer): hashes are computed via
# gc_sha256 (stdin only, see above) and the dist-info listing uses basenames
# only -- so two worktrees of the same repo, at different absolute paths,
# with byte-identical venvs, fingerprint identically. Required: the gate
# artifact directory is shared by every worktree (gc_gate_dir), and a merge
# can happen from a different worktree than the one that minted the
# artifact.
#
# FAILS CLOSED: if gc_sha256 has no backend at all, this returns 1 and
# prints nothing -- callers must NOT treat that as an empty-string
# fingerprint (two "cannot compute" states would then spuriously "match").
gc_gate_env() {
  local top="$1" verbose="${2:-}" venv pyver nodever pyvenv_h dist_h out py_exe pyvenv_pfx
  [ -n "$top" ] || return 1
  venv="$top/server/.venv"

  # v4.1.1 (#15). `dist` and `py` now come from the SAME interpreter, chosen
  # once: the venv's own python when the venv is PRESENT (pyvenv.cfg exists --
  # a present-but-broken venv, interpreter missing, does NOT fall back to a
  # system python3/python; that is a real problem on this repo, not something
  # to paper over), else whatever python3/python resolves on PATH. Previously
  # `dist` came from listing <venv>/{Lib,lib/python*}/site-packages directly,
  # so a consumer with system Python and no in-repo venv always read
  # pyvenv=absent|dist=absent|py=absent -- the one contributor that moves on
  # a dependency change was blind on exactly the repos whose gate is python.
  if [ -f "$venv/pyvenv.cfg" ]; then
    pyvenv_h=$(gc_sha256 < "$venv/pyvenv.cfg") || return 1
    if [ -x "$venv/bin/python" ]; then
      py_exe="$venv/bin/python"
    elif [ -x "$venv/Scripts/python.exe" ]; then
      py_exe="$venv/Scripts/python.exe"
    else
      py_exe=""
    fi
  else
    # v4.1.2 (spec §3): no venv -> interpreter-PREFIX identity, self-described
    # by the `sys:` prefix (the venv shape keeps its bare pyvenv.cfg hash, so
    # every existing venv-repo artifact stays valid). `absent` ONLY when no
    # interpreter resolves -- and then dist/py are absent too. The claim is
    # one-directional: pyvenv=absent => dist/py absent; a present-but-broken
    # venv reads pyvenv=<hash>|dist=absent|py=absent and the void still fires
    # via dist/py -- do not "simplify" pyvenv to derive from the interpreter.
    # Void rule unchanged; no-venv repos become ELIGIBLE (penumbra: a repo with
    # system python could never earn the extension under v4.1.1).
    py_exe=$(command -v python3 2>/dev/null)
    [ -n "$py_exe" ] || py_exe=$(command -v python 2>/dev/null)
    if [ -n "$py_exe" ]; then
      # sys.stdout.write, not print: the hash covers sys.prefix's bytes
      # exactly, with no trailing newline riding along -- and an interpreter
      # that resolves but yields nothing (a transient failure) must read
      # "absent" too, not sha256("") (a real, misleading hash of no input).
      pyvenv_pfx=$("$py_exe" -c 'import sys; sys.stdout.write(sys.prefix)' 2>/dev/null)
      if [ -n "$pyvenv_pfx" ]; then
        pyvenv_h=$(printf '%s' "$pyvenv_pfx" | gc_sha256) && [ -n "$pyvenv_h" ] && pyvenv_h="sys:$pyvenv_h" || pyvenv_h=absent
      else
        pyvenv_h=absent
      fi
    else
      pyvenv_h=absent
    fi
  fi

  if [ -n "$py_exe" ]; then
    dist_h=$("$py_exe" -c 'import site,json,os
print("\n".join(sorted(n for p in site.getsitepackages() for n in os.listdir(p) if n.endswith(".dist-info"))))' 2>/dev/null | gc_sha256) || dist_h=""
    [ -n "$dist_h" ] || dist_h=absent
    pyver=$("$py_exe" --version 2>&1)
    [ -n "$pyver" ] || pyver=absent
  else
    dist_h=absent
    pyver=absent
  fi

  nodever=absent
  command -v node >/dev/null 2>&1 && nodever=$(node --version 2>&1)

  out=$(printf 'pyvenv=%s\ndist=%s\npy=%s\nnode=%s\n' "$pyvenv_h" "$dist_h" "$pyver" "$nodever")

  if [ "$verbose" = "-v" ]; then
    printf '%s' "$out"
  else
    printf '%s' "$out" | gc_sha256
  fi
}

# gc_is_placeholder <value> -- true for an unreplaced `{{...}}`.
#
# GENERAL PRINCIPLE, and the reason this is a shared helper rather than one
# `case` arm: AN UNREPLACED PLACEHOLDER MUST NEVER WIDEN ACCESS. A hook reading
# a PROJECT_CONTEXT.md value that is still `{{...}}` has not been configured —
# it must behave exactly as if the line were absent, never treat the literal as
# data. v2.2.0 shipped the opposite for the protected set and silently
# unprotected trunk in every consumer that accepted the template.
# SUBSTRING, not whole-string: an unreplaced DEFAULT_BRANCH placeholder (the
# double-brace form) followed by ` develop` is a half-filled value, and a
# whole-string match read it as TWO literal branch names — the
# unsafe direction, since neither matches a real branch. Falling back to the
# default costs a `develop` repo one edit; treating the placeholder as data
# costs it its protection. pre-commit-test.sh has used the substring form for
# the same job since v2.1.3.
gc_is_placeholder() {
  case "$1" in
    *'{{'*'}}'*) return 0 ;;
    *)           return 1 ;;
  esac
}

# gc_resolve_trunk <repo> -- the remote's default branch, or "" when unknowable.
#
# A LOCAL ref read (`refs/remotes/origin/HEAD`): no network, so it cannot hang
# and needs no timeout wrapper -- this runs inside a PreToolUse hook.
# Deliberately NOT gc_current_branch: at push time HEAD is almost always the
# feature branch being pushed, and protecting that would block the developer's
# own push. setup-project.sh keeps a current-branch fallback because at
# BOOTSTRAP time the checkout IS the trunk; a gate firing at push time is a
# different question with a different safe answer.
# Anything unexpected -- no remote, a detached or missing origin/HEAD, a value
# that is not a plain ref name, a value that is itself a placeholder -- returns
# "" so the caller keeps its own default. Never empty output used as a set,
# never the literal.
gc_resolve_trunk() {
  gcrt=$(git -C "$1" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  gcrt=${gcrt#origin/}
  case "$gcrt" in
    ''|-*|*..*|*[!A-Za-z0-9._/-]*) printf '%s' "" ;;
    *)                             printf '%s' "$gcrt" ;;
  esac
}

# gc_fallback_protected <repo-toplevel> -- the set to protect when the repo
# has not given a usable answer: no `**Protected branches**:` line, an
# unreplaced `{{...}}` one, or an empty one.
#
# `main master` (the historical default) UNION the remote's real trunk. The
# union, not a replacement: narrowing to the resolved trunk alone would REMOVE
# protection from `main` in a repo that has both, so a fix for one exposure
# would open another. Bounded, and every member is individually justified --
# this is NOT "protect every local branch", which is the mirror of the bug it
# fixes.
# Why resolve at all: `main master` does not contain `develop`, so a repo whose
# trunk is neither was unprotected -- SILENTLY when the line was simply absent,
# which is the larger population and predates v2.2.0 entirely. v2.2.1's
# placeholder warn did not protect anything; warning is not protecting.
#
# NOTE THE ASYMMETRY, deliberately: this file fails CLOSED when it cannot read
# the COMMAND (an integrity failure -- something is trying to run and we cannot
# see what) and fails OPEN when it cannot read the REPO'S CONFIGURATION (an
# unconfigured environment -- an unknown is not a violation). Blocking pushes
# because a local ref is missing would be a worse bug than the one being fixed.
gc_fallback_protected() {
  gcpf=$(gc_resolve_trunk "$1")
  if [ -z "$gcpf" ]; then
    # The trunk is unknowable -- `origin/HEAD` is unset on any clone that never
    # ran `git remote set-head`, which is common. We do NOT block on that. But
    # a repo with no local `main` AND no local `master` is certainly not a
    # main-trunk repo, so the fallback set provably covers NO branch it has:
    # that is the one case worth saying out loud, and it cannot false-positive
    # on a main-trunk repo or on a feature branch.
    if ! git -C "$1" show-ref --verify --quiet refs/heads/main 2>/dev/null &&
       ! git -C "$1" show-ref --verify --quiet refs/heads/master 2>/dev/null; then
      json_warn_once "protected-trunk-unknown" "$(json_session "$GC_JSON")" \
        "WARN: $1 has no readable origin/HEAD and no local main or master, so the fallback protected set ('main master') covers no branch in this repo — nothing is protected. Fix with 'git remote set-head origin -a', or name your trunk on the '- **Protected branches**:' line in PROJECT_CONTEXT.md."
    fi
    printf '%s' "main master"
    return 0
  fi
  case " main master " in
    *" $gcpf "*) printf '%s' "main master"; return 0 ;;
  esac
  printf '%s' "main master $gcpf"
}

# gc_protected_branches <repo> -- the branch names the git gates protect.
#
# Optional `**Protected branches**:` line in the repo's PROJECT_CONTEXT.md,
# read with the same tolerant grep as `**Gate**:` (leading list marker,
# surrounding backticks). Names are space- or comma-separated.
# Every arm below that is NOT a configured answer resolves the same way
# (v2.2.3): "main master" PLUS the remote's real trunk. The absent arm is the
# one that matters most -- it is the pre-v2.2.0 hardcode's own blind spot, it
# affects every repo that never configured the field rather than only those
# that took the v2.2.0 template, and unlike the placeholder arm it was SILENT.
#   absent              -> the resolved fallback set, no warning: nothing is
#                          misconfigured here, the repo just never said.
#   unreplaced          -> the resolved fallback set, with one WARN: a
#   DEFAULT_BRANCH         DEFAULT_BRANCH placeholder still in its double-brace
#   placeholder            form READS as configured, so this is the case a
#                          consumer does not know they are in.
#   empty value         -> the resolved fallback set, with one WARN: an empty
#                          value is a typo or a truncated sync, not a decision.
#                          v2.2.0 treated it as an opt-out — a silent unprotect.
#   `none`              -> "" — the ONE deliberate way to protect nothing.
#                          Branch rules only: `gh pr merge` stays gated, because
#                          a PR merge is a merge whatever branch it runs on.
gc_protected_branches() {
  gcpb_top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)
  [ -n "$gcpb_top" ] || { printf '%s' "main master"; return 0; }
  gcpb_line=$(grep -E "${GC_KEY_PRE}\*\*Protected [Bb]ranches\*\*:" "$gcpb_top/PROJECT_CONTEXT.md" 2>/dev/null | head -1)
  [ -n "$gcpb_line" ] || { gc_fallback_protected "$gcpb_top"; return 0; }
  # v3.0.3 defect 3b — the leading `.*` used to be GREEDY, so a value that
  # itself contains the literal text `**Protected branches**:` again (e.g. a
  # value quoting the field name) had everything up to and including the
  # SECOND occurrence stripped, silently truncating the extracted value.
  # Anchored at the same GC_KEY_PRE the grep above uses, so the finder and the
  # extractor agree on one grammar and only the true leading marker is
  # consumed — never a later occurrence of the same text inside the value.
  gcpb=$(printf '%s' "$gcpb_line" \
    | sed -E "s/${GC_KEY_PRE}\\*\\*Protected [Bb]ranches\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" \
    | tr ',' ' ' | tr -s '[:space:]' ' ' | sed 's/^ //;s/ $//')
  if gc_is_placeholder "$gcpb"; then
    # WARN, for the same reason the empty arm does — more so. An empty value is
    # visibly empty; an unreplaced placeholder READS as configured, so this is
    # precisely the case a consumer does not know they are in. Silence here was
    # backwards.
    # ...and WARN is not enough on its own: the fallback now also covers the
    # trunk git actually reports, so a develop-trunk repo is protected while it
    # is being told to configure itself, not merely told.
    gcpb_fb=$(gc_fallback_protected "$gcpb_top")
    json_warn_once "protected-branches" "$(json_session "$GC_JSON")" \
      "WARN: **Protected branches**: in $gcpb_top/PROJECT_CONTEXT.md is still an unfilled placeholder ($gcpb) — falling back to '$gcpb_fb'. That fallback is a guess; set a real name."
    printf '%s' "$gcpb_fb"
    return 0
  fi
  case "$(printf '%s' "$gcpb" | tr 'A-Z' 'a-z')" in
    none) printf '%s' "" ;;
    "")
      # Warn ONCE: this function is called several times per gate run (the
      # block message, gc_on_main, gc_protected_alt), so a bare echo would
      # print the same line four times for one push.
      gcpb_efb=$(gc_fallback_protected "$gcpb_top")
      json_warn_once "protected-branches" "$(json_session "$GC_JSON")" \
        "WARN: **Protected branches**: is empty in $gcpb_top/PROJECT_CONTEXT.md — falling back to '$gcpb_efb'. Use 'none' to protect nothing deliberately."
      printf '%s' "$gcpb_efb"
      ;;
    *) printf '%s' "$gcpb" ;;
  esac
}

# gc_protected_alt <repo> -- the protected branches as a grep -E alternation,
# or "" when nothing is protected.
gc_protected_alt() {
  printf '%s' "$(gc_protected_branches "$1")" | tr ' ' '|'
}

# gc_gate_checked_branches <repo> -- companion spec (v3.1): GLOBS of branches
# that get ONLY the artifact-freshness check on merge, never the protected-
# branch refusals, and whose PUSH stays ungated. Built for a worktree-based
# session-branch landing route (e.g. `m113-session-2026-09-03`): dated, so a
# literal name goes stale on the first rollover and a stale gate-checked list
# fails open -- the worst way for it to fail. A glob is enough.
#
# Mirrors gc_protected_branches' grammar exactly (same GC_KEY_PRE grep, same
# sed strip, same comma/space normalisation, same gc_is_placeholder test), but
# the "not configured" arms differ: there is no protected-style fallback set
# to widen into here, so every unconfigured arm means simply "none".
#   absent      -> "" -- fully backward compatible, no warning.
#   `none`      -> "" -- the deliberate way to declare none.
#   empty value -> "" -- a typo or truncated sync is never "match everything".
#   unreplaced  -> "" with one WARN: an unfilled `{{...}}` must be REPORTED,
#   placeholder    never silently treated as absent (spec req. 3).
gc_gate_checked_branches() {
  gcgb_top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)
  [ -n "$gcgb_top" ] || { printf '%s' ""; return 0; }
  gcgb_line=$(grep -E "${GC_KEY_PRE}\*\*Gate-checked [Bb]ranches\*\*:" "$gcgb_top/PROJECT_CONTEXT.md" 2>/dev/null | head -1)
  [ -n "$gcgb_line" ] || { printf '%s' ""; return 0; }
  gcgb=$(printf '%s' "$gcgb_line" \
    | sed -E "s/${GC_KEY_PRE}\\*\\*Gate-checked [Bb]ranches\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" \
    | tr ',' ' ' | tr -s '[:space:]' ' ' | sed 's/^ //;s/ $//')
  if gc_is_placeholder "$gcgb"; then
    json_warn_once "gate-checked-branches" "$(json_session "$GC_JSON")" \
      "WARN: **Gate-checked branches**: in $gcgb_top/PROJECT_CONTEXT.md is still an unfilled placeholder ($gcgb) — treated as none, fill it or delete the line."
    printf '%s' ""
    return 0
  fi
  case "$(printf '%s' "$gcgb" | tr 'A-Z' 'a-z')" in
    none|"") printf '%s' "" ;;
    *) printf '%s' "$gcgb" ;;
  esac
}

# gc_branch_is_gate_checked <repo> <branch> -- <branch> matches one of the
# declared gate-checked GLOBS. Matched literally against the plain branch name
# gc_current_branch reports -- a `refs/heads/` prefix in the declared value is
# NOT normalised away, exactly as the case-glob match below is not a filename
# glob (`/` is an ordinary character to it, not a segment separator).
gc_branch_is_gate_checked() {
  # (task-2.7 fix round 1, review Critical F1) `set -f` BEFORE the unquoted
  # split, same shape as gc_global_options above: word splitting is wanted,
  # PATHNAME EXPANSION is not. Without it a declared value of a bare `*` (or
  # any glob that happens to collide with a real filename) is globbed against
  # the INVOKING PROCESS's cwd instead of being matched as a glob against the
  # branch name below -- an eligible branch then silently goes UNGATED,
  # depending on a cwd unrelated to the target repo. Saved/restored
  # CONDITIONALLY (unlike gc_global_options' unconditional `set +f`): a caller
  # that had deliberately set `-f` itself must not have it cleared out from
  # under it by this function returning.
  case $- in
    *f*) gcgbb_hadf=1 ;;
    *) gcgbb_hadf=0 ;;
  esac
  set -f
  gcgbb_rc=1
  # shellcheck disable=SC2086
  for gcgbb in $(gc_gate_checked_branches "$1"); do
    case "$2" in $gcgbb) gcgbb_rc=0; break ;; esac
  done
  [ "$gcgbb_hadf" = 1 ] || set +f
  return "$gcgbb_rc"
}

# gc_on_main <repo> -- the checkout sits on a protected branch.
gc_on_main() {
  b=$(gc_current_branch "$1")
  [ -n "$b" ] || return 1
  for gcpbb in $(gc_protected_branches "$1"); do
    [ "$b" = "$gcpbb" ] && return 0
  done
  return 1
}

# gc_matches_subcommand <segment> <subcommand>
#
# v4.0.1 (item 10): the two-arm shape this function used to have -- a fast
# `grep -qE "${GC_GIT_PRE}..."` path, falling back to this positional walk only
# when the fast path missed AND the segment carried `git -C` -- is gone. TWO
# CODE PATHS THAT CAN DISAGREE is exactly how item 10 happened: the fast path's
# `${GC_GIT_PRE}[[:space:]]+$2(...)` matched `commit` anywhere GC_GIT_PRE's
# global-option prefix could stretch to, including past `log --grep`, so
# `git -C /x log --grep commit` matched `commit` and ran the target repo's
# **Test** on a read-only `git log`. The walk below did not have this bug (it
# already matches only a token EQUAL to the verb), but the fast path ran FIRST
# and returned before the walk was ever consulted. FIX: delete the fast path
# and its `-C`-gated fallback trigger; the walk is now the SOLE authority for
# every caller, always. `GC_GIT_PRE` itself is retired (see the removed
# definition, formerly here at :171) — grep -n GC_GIT_PRE hooks/lib/git-cmd.sh
# hooks/*.sh shows its only remaining mentions are historical narrative in
# other hooks' comments, not live references.
#
# The strict-vs-loose distinction the old fast/fallback split existed for (a
# quoted `-C "C:/a b"` path losing its quotes in gc_segments and word-splitting
# into `-C`, `C:/a`, `b`) is still handled — the walk already treats `-C` as a
# value-consuming global regardless of what the value looks like, so it never
# needed the fast path's help for that case.
#
# v3.1 DEFECT (penumbra, historical): the fallback used to be `\bgit\b.*\b$2\b`
# -- unanchored and word-bounded over the WHOLE remainder, so `merge-base` etc.
# matched "merge" and any token merely EQUAL to the verb matched too. FIX (from
# that round, unchanged here): walk the token stream the same way gc_push_args
# does — skip `git`, skip each recognised global-option token (and its value),
# skip any other bare word without stopping, and match only a token EQUAL to
# the verb.
#
# A6.14 / N3 posture (measured, controller, 2026-09-06): `merge` and `push` are
# matched ANYWHERE a bare token equals them after the globals — `show-branch
# merge` refuses, `show-branch --merge-base a b` does not (neither word equals
# "merge"). This posture is UNCHANGED here and pinned in test-hooks.sh.
#
# v4.0.1 (item 10, F1-F3 -- outside review of the first draft of this fix):
#   F1 (compound segments): `commit` is matched ONLY at git's first non-global
#      token (GC_FIRST_TOKEN_VERBS below), so a bare `commit` operand later in
#      the stream (a --grep VALUE, a grep PATTERN) is not a match. But a first
#      non-global token that is NOT the verb used to `exit` the walk outright —
#      fine when every caller pre-splits on `&&`/`;`/`|` via gc_segments before
#      calling this function (they all do today), but nothing pinned that
#      invariant, and a single unsplit segment with two `git` invocations
#      (`git add -A && git commit -m x`) would lose the second one. FIX: on a
#      first-token mismatch in first-only mode, RESTART the state machine
#      (seen_git/want_value reset) instead of exiting, so scanning continues
#      for a later `git` token. merge/push are not first-only, so their posture
#      (scan every bare word, never restart) is untouched.
#   F2 (git-token test): `tok ~ /\\git$/` is not a portable backslash match in
#      awk — on some awk an unescaped `\g` degrades to plain `g` with a
#      warning, which would let a bare word merely ENDING in "git" (`notgit`)
#      open the walk. FIX: `is_git()` below tests equality, a `/git` suffix, or
#      a literal `\git` suffix via `substr()`, none of which depend on
#      backslash-in-regex escaping semantics.
#   F3 (quoted wrappers and substitution openers): the deleted fast path was,
#      incidentally, the only thing that matched `bash -c "git commit -m x"`,
#      `sh -lc "git commit -m x"`, `(git commit -m x)`, and — this is also how
#      gate-before-merge.sh's A6.10 command-substitution rows stayed gated —
#      `$(git merge)`, `` `git merge` ``, `<(git merge)`, `>(git merge)`: `$(`
#      and a bare verb glued to an opener are their OWN token after the `tr`
#      split (`$(git`, `` `git``, `<(git`, `>(git`), which `is_git()` alone
#      does not recognise, and a trailing `)` glued to the verb (`merge)`)
#      does not equal the verb either. gc_segments does not strip any of this.
#      FIX: strip a leading run of `"`/`(`/`$`/`<`/`>`/backtick/`'` and a
#      trailing run of `"`/`)`/backtick/`'` from every token before testing
#      it, via a regex built at runtime from `-v sq="'"` (the awk program is
#      itself single-quoted in this shell script and cannot contain a literal
#      `'`; a backtick needs no such escaping and is a literal char in the
#      class).
#
# DELIBERATE WIDENING (v4.0.1, review round 2): the F3 strip runs before the
# verb comparison for EVERY token, not only at the seen_git stage — a wrapper
# whose verb is the LAST token (`sh -c 'git commit'`) needs it there too. One
# consequence for the merge/push posture above: a quoted operand now also
# refuses, e.g. `git -C X log --grep "merge"` matches `merge` the same as the
# unquoted form does (it did not before this file's F3 fix). This is a
# fail-CLOSED widening — refusing something that previously slipped through
# quoted — not a new gap, and is pinned in test-hooks.sh.
GC_FIRST_TOKEN_VERBS=" commit "

gc_matches_subcommand() {
  case "$GC_FIRST_TOKEN_VERBS" in
    *" $2 "*) _gc_first_only=1 ;;
    *)        _gc_first_only=0 ;;
  esac
  # -v sq: a single quote handed in as a variable, because the awk program below
  # is itself single-quoted in this shell and cannot contain one literally.
  # -v bs: a literal backslash, same reason as sq but for a different awk
  # pitfall -- a `"\\git"` STRING literal is well-defined portable escaping,
  # but relying on that instead of a variable was rejected on review as one
  # more thing an unusual awk could get creatively wrong; passed in as data
  # instead, so is_git()'s backslash-path test does not depend on any awk's
  # string-literal escape handling at all.
  printf '%s\n' "$1" | tr ' \t' '\n\n' | awk -v verb="$2" -v first_only="$_gc_first_only" -v sq="'" -v bs='\' '
    BEGIN { seen_git = 0; want_value = 0; wrap = "^[\"($<>`" sq "]+|[\")`" sq "]+$" }
    $0 == "" { next }
    function is_git(t) { return (t == "git" || t ~ /\/git$/ || substr(t, length(t) - 3, 4) == bs "git") }
    {
      tok = $0
      gsub(wrap, "", tok)                  # quoted / parenthesised wrappers: bash -c "git …", (git …)
      if (tok == "") next
      if (!seen_git) {
        if (is_git(tok)) seen_git = 1
        next
      }
      if (want_value) { want_value = 0; next }
      if (tok == "-C" || tok == "-c" || tok == "--config-env" || tok == "--git-dir" ||
          tok == "--work-tree" || tok == "--namespace" || tok == "--exec-path" ||
          tok == "--attr-source" || tok == "--super-prefix") { want_value = 1; next }
      if (tok ~ /^-/) next                 # single-token global -- skip, not a match
      if (tok == verb) { print "MATCH"; exit }
      if (first_only) {                    # first non-global token was not the verb:
        seen_git = is_git(tok) ? 1 : 0     # this invocation is done; restart at the NEXT git token
        want_value = 0                     # (a compound segment: git add -A && git commit)
        next
      }
      next                                 # unrecognised bare word -- keep scanning (merge/push posture)
    }
  ' | grep -q MATCH
}

# gc_push_args <segment> -- everything after the `push` subcommand ("" if none).
#
# v3.0.3 DEFECT 1 (security fix). The regex form this replaced permitted at
# most ONE `-C` in its primary arm; a double `-C` failed it and fell to a
# GREEDY fallback (`sed -n 's/.*\bpush\b//p'`) that strips through the LAST
# word-bounded `push` in the segment. Any trailing token that merely CONTAINS
# "push" as a whole word carried that fallback past the real refspec:
# `--push-option=ci-skip`, `--receive-pack=/x/push`, `-o push-me`, or a
# refspec branch literally named `feature/push-fix:main`. Measured: from an
# unprotected cwd, `git -C /tmp/other -C <P, on a feature branch> push origin
# other:main --push-option=ci-skip` returned rc=0 through no-push-main.sh — a
# real push into protected main. This is a STRING-SUBSTITUTION-ONLY parser
# (no eval, no unquoted ~, no $(...), no sh -c) over the ALREADY-RECEIVED
# payload text; it never re-interprets the payload as a shell.
#
# FIX: walk the token stream POSITIONALLY, using the same global-option shapes
# gc_dash_c_list/gc_global_options recognise, and return everything after the
# FIRST token that IS EXACTLY `push` in subcommand position. The greedy
# fallback is deleted, not repaired -- there is no shape it covered that this
# walk does not also cover, see the note below.
#
# Unrecognised bare words are SKIPPED, not treated as an abort/subcommand
# boundary: gc_segments strips quote characters, so `git -C "C:/a b" push …`
# word-splits into `-C`, `C:/a`, `b`, `push`, … and the fragment `b` is not a
# global and is not `push`. Aborting there would silently regress the very
# case gc_matches_subcommand's own fallback exists for (a quoted `-C` path
# with a space) back into a push whose destination goes uninspected. Scanning
# past it and finding the real `push` token keeps that case covered while
# still being non-greedy: only the FIRST `push` token ends the scan.
gc_push_args() {
  printf '%s\n' "$1" | tr ' \t' '\n\n' | awk '
    BEGIN { seen_git = 0; want_value = 0; found = 0 }
    $0 == "" { next }
    {
      tok = $0
      if (!seen_git) {
        if (tok == "git" || tok ~ /\/git$/ || tok ~ /\\git$/) seen_git = 1
        next
      }
      if (found) { print tok; next }
      if (want_value) { want_value = 0; next }
      if (tok == "-C" || tok == "-c" || tok == "--config-env" || tok == "--git-dir" ||
          tok == "--work-tree" || tok == "--namespace" || tok == "--exec-path" ||
          tok == "--attr-source" || tok == "--super-prefix") { want_value = 1; next }
      if (tok ~ /^-/) next                 # single-token global (inert or attached-value)
      if (tok == "push") { found = 1; next }
      next                                 # unrecognised bare word -- keep scanning, do not abort
    }
  ' | tr '\n' ' ' | sed 's/[[:space:]]*$//'
}

# gc_targets_main_ref <push-args> <repo> -- a destination that includes a
# protected branch. `--mirror` and `--all` push every local branch, so they
# carry the protected ones even when the checkout is on a feature branch and no
# ref is named. With nothing protected (`**Protected branches**: none`) every
# destination is fine.
gc_targets_main_ref() {
  gcta=$(gc_protected_alt "$2")
  [ -n "$gcta" ] || return 1
  printf '%s\n' "$1" | grep -qE '(^|[[:space:]])--(mirror|all)([[:space:]]|=|$)' && return 0
  printf '%s\n' "$1" | grep -qE "(^|[[:space:]])(refs/heads/)?($gcta)([[:space:]]|\$)" && return 0
  printf '%s\n' "$1" | grep -qE ":[[:space:]]*(refs/heads/)?($gcta)([[:space:]]|\$)"
}

# gc_has_refspec <push-args> -- true when a destination ref is named explicitly
# (i.e. more than just the remote survives after dropping flags).
#
# A bare `HEAD` (`git push origin HEAD`) names no branch: it resolves to the
# current checkout, so it must fall through to the current-branch check rather
# than count as an explicit refspec. `HEAD:main` is a real refspec and still
# counts (and is caught by gc_targets_main_ref anyway).
gc_has_refspec() {
  n=$(printf '%s\n' "$1" | tr ' \t' '\n\n' | grep -E '^[^-][^[:space:]]*$' | grep -cvx 'HEAD')
  [ "${n:-0}" -ge 2 ]
}

# gc_push_skips_branch_check <push-args> -- pushes that are not branch pushes,
# so the implicit "current branch is main" rule must not fire.
gc_push_skips_branch_check() {
  printf '%s\n' "$1" | grep -qE '(^|[[:space:]])--(tags|delete)([[:space:]]|=|$)'
}
