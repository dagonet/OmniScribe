#!/usr/bin/env bash
# PreToolUse hook: block push to main/master
# Matcher: Bash|PowerShell
#
# Prevents direct pushes to main/master branches. Forces feature branch + PR.
#
# v2.0: the native git CLI is allowed again, so this gate parses
# tool_input.command instead of keying on the retired mcp__git-tools__git_push
# tool name. Escape hatch: <cwd>/.claude/git-guard-off.
#
# v2.2.0: the protected set is configurable. An optional PROJECT_CONTEXT.md line
#
#   - **Protected branches**: develop release
#
# replaces the default `main master`; `none` (or an empty value) protects
# nothing. The payload is parsed through hooks/lib/json.sh (node, python3 or
# jq) — with none of the three on PATH this gate fails CLOSED.

trap '[ "$?" = 127 ] && exit 2' EXIT   # v4.4.0 C2: the old registration wrapper's 127->2, now in-hook (exec/source forms cannot wrap)

# v4.4.0 C4: read and parse the payload BEFORE sourcing git-cmd.sh (1800 lines),
# so a command that cannot hold a refusal exits without loading it. ONE parse:
# gc_read_stdin takes the result over (GC_PRE_JSON / GC_PREPARSED / JP_*), so every
# refusal branch below (no parser, unparseable, unreadable command, guard-off,
# MCP tool name) runs exactly as before. The early exit needs rc 0, tool Bash or
# PowerShell and a non-empty command, so it cannot reach any of them.
jlib="$(dirname "$0")/lib/json.sh"
[ -f "$jlib" ] || { echo "BLOCKED: $jlib missing — run /sync-template step 6b (hooks/lib/json.sh)" >&2; exit 2; }
. "$jlib"
GC_PRE_JSON=$(cat)
json_payload "$GC_PRE_JSON"; GC_PREPARSED=$?
if [ "$GC_PREPARSED" = 0 ] && [ -n "$JP_CMD" ]; then
  case "$JP_TOOL" in
    Bash|PowerShell)
      # Refusal needs a git or gh word in the typed text, or in a script body
      # gc_collect_bodies reads (gc_script_body: a bash|sh|source|`.` head, or
      # gc_seg_is_ps's powershell|pwsh with its .ps1 bodies). GC_GIT_WORD_RE runs
      # on quote-stripped text (gc_git_prefilter_text), so test the quote-stripped
      # command: `g"i"t push` must continue. A backslash, `$` or a backtick may
      # build any word, and a glob character (`*`, `?`, `[`) or a `(` may name a shell
      # (`/bin/ba[s]h`, `/bin/ba@(s)h`: gc_script_body splits segments unquoted, which
      # expands globs, and extglobs too when BASHOPTS=extglob is in the environment;
      # every extglob form @( +( !( ?( *( contains `(`), so they continue too. A `.`
      # followed by whitespace or the end, anywhere (`./. run` dot-sources <dir>/. :
      # gc_script_body takes the token's basename; same test as pre-commit-test's S6).
      # Over-matches on purpose; bare `./x.sh`, `x.cmd`, `python x.py`, `node x.js`,
      # `make` and `npm run` are never scanned.
      _np_q=${JP_CMD//[\"\']/}
      _np_dot='\.([[:space:]]|$)'
      # Explicit [Gg][Ii][Tt] classes, not nocasematch: under tr_TR.UTF-8 nocasematch
      # does not fold I to i, and the early exit must never answer earlier than the old hook.
      if [[ $_np_q == *[Gg][Ii][Tt]* || $_np_q == *[Gg][Hh]* || $_np_q == *[Ss][Hh]* ||
            $_np_q == *[Ss][Oo][Uu][Rr][Cc][Ee]* || $_np_q == *[Pp][Ss]1* ||
            $_np_q == *'\'* || $_np_q == *'$'* || $_np_q == *'`'* || $_np_q == *[*?[]* ||
            $_np_q == *'('* || $_np_q =~ $_np_dot ]]; then
        :
      else
        exit 0
      fi ;;
  esac
fi

# Fail CLOSED when the sourced lib is missing: without it every gc_* helper is
# undefined, GC_CMD stays empty, and this gate would exit 0 on every push.
lib="$(dirname "$0")/lib/git-cmd.sh"
[ -f "$lib" ] || { echo "BLOCKED: $lib missing — run /sync-template step 6b (hooks/lib/git-cmd.sh)" >&2; exit 2; }
_np_jp=$JSON_PARSER   # git-cmd.sh re-sources json.sh, which resets the parser memo
. "$lib"
JSON_PARSER=$_np_jp
command -v gc_current_branch >/dev/null 2>&1 || { echo "BLOCKED: $lib is present but corrupt (gc_current_branch undefined) — this gate cannot evaluate the command, refusing" >&2; exit 2; }

gc_read_stdin --preparsed
gc_guard_off && exit 0

# Fail CLOSED on a pre-v2 settings.json: it registers this gate on the retired
# git-tools MCP tools, whose payloads carry no tool_input.command — the v2 gate
# would find nothing to parse and allow the push.
case "$GC_TOOL" in
  mcp__git-tools__git_push|mcp__git-tools__git_commit)
    echo "BLOCKED: settings.json predates this hook (MCP matcher) — restart the session after /sync-template" >&2
    exit 2 ;;
esac

# v2.2.6 round 2 — THE 14th FAIL-OPEN, same shape as pre-commit-test.sh: a bare
# `[ -n "$GC_CMD" ] || exit 0` allowed an invocation whose command arrived empty
# on a payload that parsed. See gc_cmd_unreadable in hooks/lib/git-cmd.sh for the
# state split and for why the refusal is conditional, not an outright inversion.
if gc_cmd_unreadable; then
  echo "BLOCKED: no-push-main: the payload carries a command this gate could not read, so it cannot rule out a push to a protected branch — refusing. Re-run the push. (If it repeats: create '.claude/git-guard-off' under this cwd, make the one fix, then delete it.)" >&2
  exit 2
fi

[ -n "$GC_CMD" ] || exit 0

# v4.0.3 item 12 -- widen GC_CMD to include the body of any script segment it
# invokes (`bash|sh|source|. <path>`, depth 1), BEFORE the git-token
# pre-filter just below AND the segment walk further down: both must see the
# SAME text, or a script's `git push` would pass the pre-filter's "no git
# token" fast exit before the walk that would have caught it ever runs. See
# gc_script_body / gc_augmented_cmd in hooks/lib/git-cmd.sh for the 16 KB cap
# and the depth-1/TOCTOU residuals.
# v4.3.1 S-3c: gc_dir_rule is the simple-cd rule (lib): it widens GC_CMD as above
# and refuses a gated command that changes directory in any way but one leading
# `cd <absolute dir> &&`. GC_CWD_E is the directory everything is judged in.
gc_dir_rule no-push-main "$GC_CWD" || exit 2

# v3.0.3 item 25 — exit before doing any work on a payload that cannot be gated.
# See the long note on the same block in hooks/gate-before-merge.sh: the cost is
# WORK (the segment walk and its git subprocesses), not parse, and the test is
# for a git TOKEN rather than for a leading `git push`, because after finding 62
# a gated command can be `git -P push origin main`. It reads GC_CMD and not the
# raw payload because a JSON-escaped newline puts an alnum immediately before
# `git`, which makes a raw-payload grep produce a FALSE NEGATIVE — an ungated
# exit 0 — on a newline-separated command.
# v4.3.1 G2: GC_GIT_WORD_RE (hooks/lib/git-cmd.sh) also opens the walk for git.exe, GIT and a quoted "git".
if ! printf '%s\n' "$GC_CMD" | gc_git_prefilter_text | grep -qE "$GC_GIT_WORD_RE" &&
   ! gc_has_ghpr_merge "$GC_CMD"; then
  exit 0
fi

base="$GC_CWD_E"
segments=$(gc_segments)

# np_strip_redir <args> -- <args> with shell REDIRECTION tokens removed.
#
# v3.0.2: `git push origin 2>&1` on a protected branch was ALLOWED. `2>&1`
# starts with a digit, so gc_has_refspec counted two non-flag tokens, read
# "destination named", and skipped the current-branch check — a fail-open, not
# a false positive. The counting bug is inside gc_has_refspec in
# hooks/lib/git-cmd.sh; it is compensated here, at the call site, because that
# lib is covered by the ~90-minute three-parser matrix and this shape carries no
# JSON. Same reason gate-before-merge.sh carries its own copy as
# a6_strip_redir: the duplication is deliberate and small.
#
# NOT GREEDY, and that is the point: dropping everything after a `>` would turn
# `git push origin main 2>&1` into a refspec-free push and lose the explicit
# protected destination. Only a token that is ITSELF a redirection operator is
# dropped — with its target attached (`2>&1`, `>file`), or bare (`>`, `2>`)
# plus exactly the one token that follows it. The empty-token arm is first
# because `tr` emits an empty field for a double space, which would otherwise
# consume the skip and leak the redirect target back in as an operand.
np_strip_redir() {
  printf '%s\n' "$1" | tr ' \t' '\n\n' | awk '
    $0 == "" { next }
    skip == 1 { skip = 0; next }
    /^([0-9]*|&)(>>|>|<).+$/ { next }
    /^([0-9]*|&)(>>|>|<)$/ { skip = 1; next }
    { print }
  ' | tr '\n' ' '
}

# v3.0.1 (consumer report): the no-refspec path below reads the CURRENT BRANCH,
# which is ambient state this hook resolves BEFORE the command runs — so
# `git checkout main && git push` was evaluated on the feature branch and let
# through. The refspec paths are immune by construction: they key on the
# argument, never on ambient state.
# ORDER, not co-presence: only a branch change that PRECEDES the push matters,
# because only that changes where the push lands. `git push origin feature/x &&
# git checkout main` is unaffected, and must stay so.
moved=0

while IFS= read -r seg; do
  [ -n "$seg" ] || continue

  # A clause that can move HEAD to another branch. `--` means "everything after
  # is a path", so `git checkout -- file` restores files without moving HEAD and
  # must not arm the refusal.
  #
  # KEYED ON THE TARGET, not on the presence of a checkout: `git checkout
  # feature/z && git push` lands nothing on a protected branch, and refusing it
  # would be a false positive on ordinary work. The target is an ARGUMENT — in
  # the payload, not ambient state — which is the whole point. Last one wins.
  # 0 = harmless, 1 = moves onto a protected branch, 2 = target unresolvable.
  if { gc_matches_subcommand "$seg" "checkout" || gc_matches_subcommand "$seg" "switch"; } &&
     ! printf '%s\n' "$seg" | grep -qE '(^|[[:space:]])--([[:space:]]|$)'; then
    mvargs=$(printf '%s\n' "$seg" | sed -nE 's/.*[[:space:]](checkout|switch)([[:space:]]|$)/\2/p' | head -1)
    mvtarget=$(printf '%s\n' "$mvargs" | tr ' \t' '\n\n' | grep -E '^[^-][^[:space:]]*$' | head -1)
    # Trim: gc_segments splits on `&&`/`;`/`|`, which leaves a leading or
    # trailing space on the clause either side of the delimiter -- interior
    # spacing (the checkout's own arguments) is untouched.
    mv_seg=$(printf '%s' "$seg" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$mvtarget" in
      ''|*[!A-Za-z0-9._/-]*) moved=2 ;;
      *)
        moved=0
        for mvp in $(gc_protected_branches "$(gc_repo_for "$seg" "$base")"); do
          [ "$mvtarget" = "$mvp" ] && moved=1
        done ;;
    esac
    continue
  fi

  gc_matches_subcommand "$seg" "push" || continue

  # v3.0.3 (finding 62): a global before `push` used to make the line above
  # false, so this gate exited 0 having evaluated nothing — `git --no-pager
  # push origin main` and `git -P push origin main` were ALLOWED past BOTH git
  # gates, measured on four hosts. v4.0.1: the lib's positional walk in
  # gc_matches_subcommand is now the SOLE authority (the GC_GIT_PRE fast path
  # that originally fixed this is retired -- see hooks/lib/git-cmd.sh) and it
  # finds the subcommand regardless of the globals; the globals are then
  # classified separately, here, by the same gc_global_options the merge gate
  # uses. An inert global (`--no-pager`, `-P`, `--paginate`, …) falls through to
  # the push checks below and gets the normal verdict — matching is not
  # allowing, and a skip is not a verdict.
  npg=$(gc_global_options "$seg")
  if [ "$npg" != ok ]; then
    case "$npg" in
      refuse:*) npgopt="${npg#refuse:}" ;;
      env:*)    npgopt="${npg#env:}=" ;;
    esac
    {
      echo "BLOCKED: this push carries the global option '$npgopt' before the subcommand, which changes what the command RESOLVES to (config, repo, ref namespace, or binaries), or is unknown to this gate."
      echo "  matched segment: $seg"
      echo "  verdict: refused. Allowed globals: -C <path>, --no-pager, -P, --paginate, --no-optional-locks, --literal-pathspecs, --no-lazy-fetch."
      echo "Could not determine: this check reads the branch you are on and the refspec you typed. An inline '$npgopt' can re-point remote.<name>.url or branch.<name>.remote for this one command, so the destination it computes is not the one this hook can see."
      echo "Re-run the push WITHOUT the '$npgopt' option. If the setting is one you genuinely need, put it in the repository's configuration in a separate call, where a reader can see it."
      echo "(If this is a false positive: create '.claude/git-guard-off' under this cwd, make the one push, then delete it.)"
    } >&2
    exit 2
  fi

  # v3.0.3 defect 1: repeated `git -C` is folded in argv order by gc_repo_for.
  # Before that fix this gate took the FIRST operand, so
  # `git -C <feature repo> -C <protected repo> push` resolved to the feature
  # repo and returned 0 from either cwd — a measured live bypass, and the
  # mirror-image false positive in the other operand order. A fold that does
  # not resolve is the cannot-determine case and is refused; a SINGLE `-C` into
  # a missing directory keeps the documented fall-back-to-cwd behaviour.
  npdu_out=$(gc_dash_c_unresolved "$seg" "$base")
  if [ -n "$npdu_out" ]; then
    npdu_kind=$(printf '%s\n' "$npdu_out" | sed -n 1p)
    npdu=$(printf '%s\n' "$npdu_out" | sed -n 2p)
    if [ "$npdu_kind" = cannot-determine ]; then
      # v3.0.3 defect 2 -- a different fact from "does not resolve": the
      # operand contains an unexpanded shell expression this hook cannot know
      # the value of without executing it (never done here).
      {
        echo "BLOCKED: hook cannot DETERMINE the -C target (contains an unexpanded shell expression): $npdu"
        echo "  matched segment: $seg"
        echo "This push names a -C operand this hook cannot resolve by string substitution"
        echo "alone. Pass a literal or absolute path, or one of \$HOME/\$USERPROFILE/~."
      } >&2
      exit 2
    fi
    {
      echo "BLOCKED: hook could not resolve \`-C $npdu\`; if git can, pass an absolute path."
      echo "  matched segment: $seg"
      echo "This push carries more than one 'git -C' and NOT ONE of them resolves, so there"
      echo "is no candidate repository to judge. A fold with at least one resolvable step is"
      echo "judged on the strictest candidate instead of being refused."
    } >&2
    exit 2
  fi

  repo=$(gc_repo_for "$seg" "$base")
  args=$(np_strip_redir "$(gc_push_args "$seg")")

  # 1. An explicit protected destination is always a block.
  if gc_targets_main_ref "$args" "$repo"; then
    echo "BLOCKED: pushing to a protected branch ($(gc_protected_branches "$repo")) is not allowed. Use a feature branch and open a PR." >&2
    exit 2
  fi

  # 2. No explicit refspec -> the push follows the current branch.
  if ! gc_has_refspec "$args" && ! gc_push_skips_branch_check "$args"; then
    if [ "$moved" != 0 ]; then
      {
        if [ "$moved" = 1 ]; then
          # Task 2.6 (penumbra's sentence, verbatim): name the checkout clause
          # that has not run yet, rather than the vaguer "an earlier clause
          # ... checks out '$mvtarget'" -- the reader needs the exact clause
          # to split out, not just the branch it targets.
          echo "refused: push evaluated on branch '$(gc_current_branch "$repo")' — the '$mv_seg' earlier in this call has not run when this hook fires; split the call: checkout first, then push alone."
        else
          echo "BLOCKED: this push names no refspec, so it follows the current branch — and an earlier clause in the same command changes that branch to a target this hook cannot resolve ('${mvtarget:-<none named>}'), so which branch it pushes to cannot be determined."
        fi
        echo "Do the checkout and the push as SEPARATE calls, or name the destination: 'git push origin <branch>' keys on its argument and never consults the current branch."
        echo "(If this is a false positive: create '.claude/git-guard-off' under this cwd, make the one push, then delete it.)"
        # v3.0.3 (queue item 4, consumer-authored, verbatim). BOTH no-refspec
        # exits carry it: the paragraph is about how the DESTINATION is
        # resolved, and both of these are exits taken because no refspec named
        # one. The explicit-refspec block at the top of this loop does not —
        # there the argument IS the destination and there is no blind spot.
        echo "Could not determine: this check reads the branch you are on and the refspec you typed. It cannot see the remote's own push configuration, so a push whose destination is decided by remote.<name>.push or push.default rather than by your argument may land somewhere this check never evaluated."
      } >&2
      exit 2
    fi
    if gc_on_main "$repo"; then
      {
        echo "BLOCKED: pushing to a protected branch is not allowed (current branch of $repo is $(gc_current_branch "$repo")). Use a feature branch and open a PR."
        echo "Could not determine: this check reads the branch you are on and the refspec you typed. It cannot see the remote's own push configuration, so a push whose destination is decided by remote.<name>.push or push.default rather than by your argument may land somewhere this check never evaluated."
      } >&2
      exit 2
    fi
  fi
done <<GC_SEGMENTS
$segments
GC_SEGMENTS

exit 0
