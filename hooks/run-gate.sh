#!/usr/bin/env bash
# Gate runner (invoked by developers/PO, not registered as a hook):
#   bash hooks/run-gate.sh
#
# Reads the Gate command from PROJECT_CONTEXT.md ("**Gate**: <command>",
# with or without a leading list marker) and runs it. On success, writes
# the gate artifact that hooks/gate-before-merge.sh checks before allowing
# a PR merge:
#
#   <common git dir>/gate/last-pass.<HEAD sha>.json, or last-pass.tree-<tree>.json for a run whose tree differs from HEAD^{tree} (v4.3.1 G4)  (v4.0.1, item 17 -- see
#   gc_gate_dir's header note in hooks/lib/git-cmd.sh for why this is the
#   COMMON git dir, not the toplevel of the invoking checkout/worktree: it is
#   the one location every worktree of a repo resolves to, so a gate run from
#   a linked worktree is visible to a merge attempted from the main checkout.
#   Falls back to the pre-4.0.1 <toplevel>/.gate on git < 2.31.)
#   {"sha":"<HEAD sha>","tree":"<working-tree hash>","branch":"<branch>",
#    "ts":"<UTC ISO-8601>","status":"pass"}
#
#   The sha suffix (not a single fixed filename) is deliberate: the directory
#   is now SHARED by every worktree, so two worktrees gating concurrently must
#   not clobber each other's artifact. gate-before-merge.sh looks up the exact
#   filename for the sha it is merging first, then falls back to a tree scan
#   (see its header note). Artifacts older than 24h are pruned on the next
#   successful run (below) -- always well past GC_GATE_TTL_S, the freshness
#   window gate-before-merge.sh enforces, so pruning can never delete an
#   artifact a merge would still honour.
#
# ARTIFACT DOC NOTE (v3.0.4 item A4, FIXED v3.1): the TREE arm of
# gate-before-merge's freshness check IS LOAD-BEARING, not a nice-to-have --
# on a new branch's FIRST commit the SHA arm can never match (a PreToolUse
# hook runs BEFORE the commit it gates exists, so no sha it could record is
# the one the commit will get; consumer report, yutraffic), so the tree arm
# is the only one that can pass at all there. Through v3.0.4 that arm hashed
# via `git add -A` into a temporary index, which therefore INCLUDED UNTRACKED
# FILES: build output, an editor swap file, a leftover fixture. Any of those
# changed the hash, so tree-freshness could report stale after a real sha
# change while untracked content sat in the working tree, or mask staleness
# the SHA arm would otherwise have caught cleanly on its own. v3.1: both this
# script and hooks/pre-commit-test.sh hash via `git add -u -- .` instead --
# untracked files no longer enter the hash at all. A mutation batched into the
# SAME Bash call as the commit (the case pre-commit-test.sh's own
# last-precommit.json `tree` field discriminates, separately from this one)
# remains exactly as before: tracked-file staleness is still caught.
#
# On failure, any existing artifact is deleted and the script exits nonzero:
# 1 for an ordinary red gate (retry after fixing), GC_TERMINAL_RC (78) when the
# failure is terminal — a configuration the gate command cannot succeed under,
# where "re-run it" is the wrong advice. See the exit-code conventions block in
# hooks/lib/git-cmd.sh.
#
# THE TERMINAL CONTRACT IS PUBLIC (v2.3.0). A **Gate** command — typically a
# preflight chained ahead of the real gate, `bash preflight.sh && <gate>` — can
# declare its OWN terminal condition: print the remedy to stderr, touch
# $RUN_GATE_TERMINAL, exit 78. The clamp below then passes the 78 through
# instead of collapsing it to 1, and the terminal branch stays silent so the
# consumer's remedy is the last thing on screen. Worked example and the naming
# commitment this implies: docs/verification.md.
# No-op (exit 0) when the Gate field is missing or still a {{...}} placeholder,
# so templates degrade gracefully before a project configures its gate.
#
# v2.1.3 fix round 1 (review): a project whose **Gate** command itself invokes
# this script (e.g. "bash hooks/run-gate.sh" -- a copy/paste mistake, or a
# gate that shells out to a wrapper that shells out here) would otherwise
# recurse until the process/fd limit kills it. RUN_GATE_ACTIVE guards against
# that: it is exported before the gate command runs and checked on entry.

#
# v2.2.5 (consumer report): the guard was safe but its follow-on advice was
# circular — the outer layers appended "fix the failures and re-run" to a
# condition that no amount of re-running can change. GC_TERMINAL_RC, defined
# locally for the same standalone reason as GC_KEY_PRE below, is how a caller
# tells the two apart. See the exit-code conventions block in
# hooks/lib/git-cmd.sh; scripts/verify-template-consistency.sh asserts the two
# definitions stay in step.
GC_TERMINAL_RC=78

if [ "${RUN_GATE_ACTIVE:-}" = "1" ]; then
  echo "BLOCKED: **Gate** must not invoke run-gate.sh itself" >&2
  echo "Edit '**Gate**:' in PROJECT_CONTEXT.md to your real build/test commands — run-gate.sh RUNS that value, so it cannot BE that value." >&2
  # PROVENANCE MARKER, and it is load-bearing (v2.2.5 round 3). The OUTER
  # run-gate.sh clamps a gate command's 78 to 1, because an arbitrary consumer
  # gate that exits 78 for its own reasons must not inherit the terminal remedy
  # text. But in the self-reference case the gate command IS run-gate.sh, so the
  # clamp would swallow the one signal item K exists to deliver. The exit code
  # carries a VALUE; what the outer layer needs is PROVENANCE. This file, and
  # only this file, touches the marker the outer exported — at any nesting depth,
  # since the variable is inherited through wrappers too. See the clamp below.
  [ -n "${RUN_GATE_TERMINAL:-}" ] && : > "$RUN_GATE_TERMINAL"
  exit "$GC_TERMINAL_RC"
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  echo "Usage: bash hooks/run-gate.sh"
  echo ""
  echo "Runs the Gate command from PROJECT_CONTEXT.md (**Gate**: <command>)."
  echo "Green: writes <common git dir>/gate/last-pass.<sha>.json, or last-pass.tree-<tree>.json when run before the commit (checked by gate-before-merge.sh) and prints GATE PASS <sha>."
  echo "Red:   deletes the artifact and exits 1 (78 when the failure is terminal — see hooks/lib/git-cmd.sh)."
  echo "No Gate configured: prints GATE SKIP and exits 0."
  echo ""
  echo "Your Gate command can declare its own terminal condition: print the remedy"
  echo "to stderr, touch \$RUN_GATE_TERMINAL, and exit with the terminal code."
  echo "Worked example: docs/verification.md."
  exit 0
fi

CWD=$(pwd)
REPO_TOP=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_TOP" ]; then
  # TERMINAL (v2.2.5 round 3): re-running this from the same cwd cannot ever make
  # that directory a git repository. Before this it exited 1, so pre-commit-test
  # appended "re-run it and fix the failures" — item K's circular advice, in a
  # guard that already existed rather than a hypothetical future one. The class
  # is TERMINAL, not "configuration": this one is an ENVIRONMENT error and 78
  # covers both (see the exit-code conventions in hooks/lib/git-cmd.sh).
  echo "GATE ERROR: not inside a git repository" >&2
  echo "Run 'bash hooks/run-gate.sh' from inside the checkout — cd to the repository and re-run it there." >&2
  exit "$GC_TERMINAL_RC"
fi

# GC_KEY_PRE, defined locally: this script is deliberately standalone (it must
# run with no JSON parser on PATH, which sourcing hooks/lib/git-cmd.sh would
# forbid), so it repeats the constant rather than importing it. The definition
# and the reason live in the header note on GC_KEY_PRE in hooks/lib/git-cmd.sh;
# scripts/verify-template-consistency.sh asserts the two stay in step.
GC_BOM=$(printf '\357\273\277')
GC_KEY_PRE="^(${GC_BOM})?[-*[:space:]]*"

# GC_GATE_TTL_S and gc_gate_dir, defined locally for the same standalone
# reason as GC_KEY_PRE above (v4.0.1, item 17). The definitions and the
# reasons live in hooks/lib/git-cmd.sh; scripts/verify-template-consistency.sh
# asserts all three stay in step. v4.0.3 item 8: gc_gate_dir refuses an
# unresolved target instead of printing /.gate with a false git-version
# warning -- see the header note on the git-cmd.sh copy.
GC_GATE_TTL_S=3600
gc_gate_dir() {
  local top common
  [ -n "$1" ] || return 1
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 1   # unresolved target: nothing
  common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || common=""
  if [ -n "$common" ]; then printf '%s/gate\n' "$common"; return 0; fi
  echo "WARN: git < 2.31: gate artifacts stay at <toplevel>/.gate (per-worktree)" >&2
  printf '%s/.gate\n' "$top"
}

# GC_GATE_PRUNE_S (v4.0.3, R4) -- same standalone-copy reason as GC_GATE_TTL_S
# and gc_gate_dir above. The definition and the reason live in
# hooks/lib/git-cmd.sh; scripts/verify-template-consistency.sh asserts the
# two copies agree.
GC_GATE_PRUNE_S=$(( GC_GATE_TTL_S * 24 ))

# gc_sha256 / gc_gate_env, defined locally for the same standalone reason as
# GC_GATE_TTL_S/gc_gate_dir above (v4.0.3 item 13). Definitions and the
# reasons live in hooks/lib/git-cmd.sh; scripts/verify-template-
# consistency.sh asserts the copies agree.
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

# Read Gate command from PROJECT_CONTEXT.md. Tolerates: an optional leading
# UTF-8 BOM, leading "- " / "* " list
# markers, the "**Gate Command**:" label style (java/python variants), and
# surrounding backticks — several variants write commands as `cmd`.
# v3.0.3: anchored at GC_KEY_PRE like the other four field extractors — the
# fifth site, found by enumeration after 3b; a greedy `.*` here let a
# PR-editable Gate value truncate the command the gate runs.
GATE_CMD=$(grep -E "${GC_KEY_PRE}\*\*Gate( Command)?\*\*:" "$REPO_TOP/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Gate( Command)?\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)

# No-op: no PROJECT_CONTEXT.md or no Gate command configured
if [ -z "$GATE_CMD" ]; then
  echo "GATE SKIP (no Gate command configured in PROJECT_CONTEXT.md)"
  exit 0
fi

# No-op: placeholder not yet filled in
case "$GATE_CMD" in
  *\{\{*\}\}*)
    echo "GATE SKIP (Gate command is still a template placeholder)"
    exit 0
    ;;
esac

# v4.3.0 A2 -- **Gate extra** (opt-in). Sound only if Gate == Test && Gate
# extra (R-A, spec Part A2/plan refinement R-A); otherwise ignore it and run
# the full Gate exactly as before. Read here, once, with the same
# GC_KEY_PRE-anchored field grammar the other extractors on this page use.
# v4.3.0 fix round 1, M2: `( Command)?` tolerance, same as GATE_CMD's own
# extraction just above (java/python variants spell it "**Test Command**:") --
# rg_field is generic over the key name, so this is one change for every key
# it reads (**Gate extra**, **Test**), not a second copy of the tolerance.
rg_field() { grep -E "${GC_KEY_PRE}\*\*$1( Command)?\*\*:" "$REPO_TOP/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*$1( Command)?\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1; }
rg_norm() { tr -s ' \t' '  ' | sed 's/^ //;s/ $//'; }
GATE_EXTRA=$(rg_field 'Gate extra'); RG_TEST=$(rg_field 'Test')
case "$GATE_EXTRA$RG_TEST" in *\{\{*\}\}*) GATE_EXTRA="" ;; esac
if [ -n "$GATE_EXTRA" ] && [ "$(printf '%s' "$GATE_CMD" | rg_norm)" != "$(printf '%s && %s' "$RG_TEST" "$GATE_EXTRA" | rg_norm)" ]; then
  echo "run-gate: WARN **Gate extra** is set but **Gate** is not exactly '<Test> && <Gate extra>' -- ignoring **Gate extra**, running the full Gate" >&2
  GATE_EXTRA=""
fi

# v4.3.0 fix round 2, S-10 (C2 re-review). Round 1 shipped a DENYLIST over
# whitespace-separated tokens, and the re-review reproduced NINE wrong-PASS
# bypasses of it, each giving a split rc 0 where the plain Gate gives rc 1:
# `c\d sub` and `\cd sub` (a backslash inside the word defeats an exact-token
# match -- bash removes it before running "cd" for real), `true&&cd sub`
# (no space around `&&` used to leave "true&&cd" as ONE token under
# whitespace-only splitting), `eval cd\ sub`, `{cd,sub}` (brace expansion
# produces "cd sub" only once bash itself parses the word), `cd${IFS}sub`,
# `printf -v D sub/ && bash ${D}x.sh`, `: ${D:=sub/} && bash ${D}x.sh`,
# `read D < d.txt && bash ${D}x.sh`, `hash -p ./sub/x.sh bash && bash x.sh`.
# A DENYLIST over tokens cannot enumerate every way shell syntax can build or
# hide a word; this is now a real ALLOW-list -- refuse anything outside a
# small, enumerated safe character set BEFORE tokens are even considered, so
# none of the above ever reaches the token/verb scan in the first place.
rg_stateless() {
  rgst_t="$1"
  rgst_trim=$(printf '%s' "$rgst_t" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
  [ -n "$rgst_trim" ] || return 1
  # Character allow-list: letters, digits, `_ . / : @ % + , = SPACE -` ONLY.
  # Excludes, by construction and not by enumeration, EVERY mechanism above:
  # backslash, `$`, `{` `}`, `<` `>`, `*` `?` `[` `]`, `~`, `&` `|`, `;`, a
  # backtick, a quote, `(` `)`. Anchored full-string, not a substring test.
  if ! printf '%s' "$rgst_trim" | grep -Eq '^[A-Za-z0-9_./:@%+,= -]+$'; then
    return 1
  fi
  set -f
  # shellcheck disable=SC2086
  set -- $rgst_trim
  set +f
  # v4.3.0 fix round 4, S-12: FIRST-WORD ALLOW-list. Rounds 1-3 denylisted
  # shell verbs and leaked every round (9, then 2, then 1 wrong-PASS bypass;
  # the last one `coproc sleep 5 && jobs -x bash chk.sh %1`, where the job
  # table carries the coproc across `&&`). State can only cross a split
  # boundary through the shell that runs the parts, and a part whose first
  # word is an external program (or a relative path to a script) changes no
  # state in that shell. So the first word must be one of these names, or a
  # relative path: contains `/`, no `..`, not absolute. Anything else --
  # every builtin, keyword and function name -- refuses. Keep this `case`
  # alternation on ONE line (a continuation silently corrupts a pattern).
  case "$1" in
    bash|sh|python|python3|node|npm|npx|pnpm|yarn|cargo|dotnet|go|mvn|gradle|./gradlew|pytest|make|ctest|rake|bundle|composer|php|ruby|perl|deno|bun) ;;
    /*|*..*) return 1 ;;
    */*) ;;
    *) return 1 ;;
  esac
  for rgst_tok in "$@"; do
    # A word beginning with `%` is a job spec to `jobs -x`, `kill`, `wait`,
    # `fg`, `bg` (S-12 rule c). Refused at EVERY position, the first word
    # included (stricter than the ruling, which names later words only: a
    # first word like `%1/x` is a relative path to the list above).
    case "$rgst_tok" in
      %*) return 1 ;;
    esac
    # A NAME=value OR NAME+=value assignment token (env-var-style prefix;
    # fix round 3, S-11 -- the round-2 regex missed the `+=` append form,
    # measured: `PATH+=:sub && bash pcheck.sh` split rc 0 where the plain
    # Gate's own `PATH+=:sub` really changed PATH and the plain Gate's own
    # pcheck.sh correctly saw it and failed). Checked at any position, not
    # only the first, per this function's whole stance: doubt refuses.
    if printf '%s' "$rgst_tok" | grep -Eq '^[A-Za-z_][A-Za-z0-9_]*\+?='; then
      return 1
    fi
    # Verb denylist, widened (fix round 2): `eval`, `exec`, `trap`, `read`,
    # `printf`, `hash`, and the other indirection/builtin-state verbs the
    # bypasses above actually used, alongside round 1's cd/pushd/popd/
    # export/source/./set/umask/alias/unset/shopt. This scan is now a
    # SECOND, independent line of defence behind the character allow-list
    # above (most of the bypasses were already stopped there) -- kept
    # because a token-shaped denylist still catches a plain, otherwise
    # allow-list-legal "eval something" that the character check alone
    # would pass.
    case "$rgst_tok" in
      cd|pushd|popd|export|source|.|set|umask|alias|unset|shopt|eval|exec|trap|read|printf|hash|ulimit|builtin|command|declare|typeset|local|readonly|let|mapfile|readarray|getopts|enable)
        return 1 ;;
    esac
  done
  return 0
}

# Legs, computed ONCE here (not re-derived in the run section) so the
# state-free check and the actual run walk the IDENTICAL list. Split on `&&`
# with optional surrounding whitespace (fix round 2: round 1 split on the
# literal ` && ` only, which is exactly what let `true&&cd sub` through as
# one un-inspected token). Every part -- **Test** and every leg -- is
# validated by rg_stateless below; the character allow-list it applies makes
# a SEPARATE "is this ambiguous to split" exclusion unnecessary (any of the
# old ambiguity metacharacters -- a quote, `$(`, a backtick, `(`, `<<` -- is
# already outside the allowed character set and refuses the whole thing).
if [ -n "$GATE_EXTRA" ]; then
  RG_LEGS=$(printf '%s' "$GATE_EXTRA" | awk '{gsub(/[[:space:]]*&&[[:space:]]*/,"\n")}1')
  RG_UNSAFE=0
  # v4.3.0 fix round 3, S-11 (I2 re-review -- "empty legs skip refusal
  # instead of triggering it"). A LEADING or TRAILING `&&` (`&& bash x.sh`;
  # `bash x.sh &&`) names an EMPTY part at that end. The trailing case is
  # invisible to the split-then-inspect walk below: `$(...)` command
  # substitution strips ALL trailing newlines from sed's own output, and the
  # substitution that produces the empty final leg does so BY INSERTING that
  # trailing newline -- so the empty leg it just created is exactly what
  # gets stripped before $RG_LEGS is ever read. Checked directly against the
  # (whitespace-trimmed, but otherwise UNSPLIT) **Gate extra** text, which
  # command substitution has not yet had a chance to mangle. MEASURED:
  # `bash x.sh &&` and `&& bash x.sh` both split rc 0 pre-fix where the
  # plain Gate is a bash syntax error (rc 1).
  RG_EXTRA_TRIMMED=$(printf '%s' "$GATE_EXTRA" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')
  case "$RG_EXTRA_TRIMMED" in
    '&&'*|*'&&') RG_UNSAFE=1 ;;
  esac
  rg_stateless "$RG_TEST" || RG_UNSAFE=1
  # v4.3.0 fix round 3: NO `continue` on an empty/whitespace-only leg -- it
  # must be REFUSED, not silently skipped. `rg_stateless` already refuses an
  # empty trimmed string on its own (`[ -n "$rgst_trim" ] || return 1`), so
  # simply always calling it is enough; skipping the call is what let a
  # MIDDLE empty leg (`bash x.sh && && bash x.sh`, which the split above DOES
  # preserve as a genuine blank line) vanish unchecked.
  while IFS= read -r rg_chk_leg; do
    rg_stateless "$rg_chk_leg" || RG_UNSAFE=1
  done <<RG_STATE_CHECK
$RG_LEGS
RG_STATE_CHECK
  if [ "$RG_UNSAFE" = 1 ]; then
    echo "run-gate: WARN **Test** or a **Gate extra** leg is not an allow-listed state-free simple command (character set, first word, job spec, verb, assignment, or an empty leg) -- ignoring **Gate extra**, running the full Gate" >&2
    GATE_EXTRA=""
  fi
fi

HEAD_SHA=$(git -C "$CWD" rev-parse HEAD 2>/dev/null)
BRANCH=$(git -C "$CWD" branch --show-current 2>/dev/null)
ARTIFACT_DIR=$(gc_gate_dir "$CWD")
# v4.0.3 item 8: gc_gate_dir now refuses (empty stdout, rc 1) rather than
# printing a plausible-looking path for an unresolved target. REPO_TOP was
# already validated above, so this is not expected to fire from this call
# site in practice -- defensive, not reachable by a known live path. TERMINAL
# (not 1): re-running from the same cwd cannot make the target resolve any
# differently, same class as the "not inside a git repository" guard above.
if [ -z "$ARTIFACT_DIR" ]; then
  echo "GATE ERROR: gate directory unresolved" >&2
  exit "$GC_TERMINAL_RC"
fi
# Sha-keyed filename, not a single fixed name (v4.0.1, item 17): the
# directory above is now shared by every worktree of the repo, so a fixed
# name would let two worktrees gating concurrently clobber each other's
# artifact. See the header note for the full rationale. ARTIFACT itself is
# assigned below, once the gated tree is known (v4.3.1 G4).

echo "GATE: running: $GATE_CMD"
# This `exit 1` DELIBERATELY STAYS 1 and is not a terminal 78 (v2.2.5 round 3):
# a failing cd to a path git JUST resolved is an environment FAULT — a race, a
# permissions change, an unmounted share — not a settled condition. Retrying can
# legitimately succeed, so "re-run it" is the right advice here and this is not
# an inconsistency to tidy up.
cd "$REPO_TOP" || exit 1

# v2.1.3 fix round 1 (Critical 2 / penumbra #2c): key the artifact on the
# INDEX tree, not just HEAD's sha. At PreToolUse commit time (pre-commit-test.sh
# invoking this script before the `git commit` runs) the index tree is the tree
# the commit is about to get -- so gate-before-merge.sh can accept an artifact
# whose tree matches HEAD^{tree} even though its sha is the PARENT commit's,
# not the new one. Accepted miss: `git commit -a` or a commit with extra
# `git add` after this ran stages more than the index snapshot we hashed here
# -- that produces a tree mismatch too, and the merge gate falls back to
# requiring a fresh run, exactly as before this fix.
#
# v2.1.5 (consumer feedback: Yutraffic PR #223 e59e6fd vs 567f0d1, panoscribe
# PR #123): key the artifact on the WORKING TREE, not the index. The PreToolUse
# hook fires before a chained `git add ... && git commit` stages anything, so
# the v2.1.3 index tree was the PARENT tree and the v2.1.3-round-2 `git diff
# --quiet` guard recorded no tree at all -- the artifact matched nothing and the
# single-run merge path never fired for agents, who chain add+commit habitually.
#
# A temp index (a copy of the real one, so unchanged paths need no re-stat) is
# refreshed with `add -u -- .` (v3.1) and hashed. The REAL index is never
# touched. `add -u` only UPDATES paths already IN the index with their
# current worktree content -- it never ADDS a path that is not there. Read
# that as "whatever the real index held at copy time, plus fresh content for
# what it already held", not as "only files HEAD already tracks": a file
# `git add`ed BEFORE this hook fires -- a staged NEW file, not yet in any
# commit -- is already in the real index at copy time, so it is already in
# the temp index too, and `add -u` leaves it there. That staged new file IS
# in the hash, and is blessed by design (fix round 1, item 13', measured:
# staged new file -> in; never-staged file -> out; modified tracked file ->
# worktree version, not the last-committed one). Only a file that was never
# staged at all -- still fully untracked at hook time -- is outside the temp
# index from the start and stays outside no matter what `add -u` does.
#
# Consequently `git add -u -- . && git commit`, `git commit -a`, and separate
# add/commit calls of already-tracked files all yield `HEAD^{tree} == tree`.
# A PARTIAL-add commit mismatches by design: the committed tree is not what
# was gated, so gate-before-merge.sh correctly demands a fresh run. An
# UNTRACKED file present at gate time no longer enters the hash at all (v3.1)
# -- committing it anyway (`git add -A` on an otherwise tracked-only commit)
# now mismatches too, which is the point: an untracked file is no longer
# something this gate can bless sight-unseen.
#
# CAVEAT -- the hash is taken BEFORE the gate command runs (deliberately: a
# gate that fails must not have its own mutations blessed). So a gate that
# MUTATES a TRACKED file makes the following commit mismatch anyway (a
# formatter in the gate rewriting tracked files). Gate-generated output that
# is untracked no longer perturbs the hash either way (v3.1) -- the prior
# caveat about it leaking into the NEXT run's hash no longer applies, though
# gitignoring it remains good practice regardless.
#
# `rev-parse --git-path index` (not a hardcoded .git/index) is what makes this
# work in a LINKED WORKTREE, where the index lives at
# .git/worktrees/<name>/index -- coder/tester run under `isolation: worktree`.
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT
TMPIDX="$TMPD/index"   # must not pre-exist: git rejects a 0-byte index
# v4.3.0 fix round 1, S-8 (I1/I2). `--path-format=absolute`: the BARE
# `--git-path` prints a path RELATIVE TO THE CALLING PROCESS'S OWN cwd in a
# plain (non-worktree) checkout (the same quirk gc_gate_dir's header note
# documents for `--git-common-dir`) -- from a cwd other than REPO_TOP that
# relative path resolves to a nonexistent file, `cp` used to fail SILENTLY
# (`2>/dev/null || true`), and the subsequent `add -u` on a freshly-created
# EMPTY index does nothing at all, producing the git EMPTY TREE
# (4b825dc642cb6eb9a060e54bf8d69288fbee4904) instead of a real measurement.
# `-p` preserves the REAL index file's timestamps on the copy rather than
# stamping "now" -- without it, git's own racy-git protection (which compares
# an index entry's mtime against the index file's own mtime) can misjudge a
# same-second edit as already reflected in the copy (measured stale 7 of 8).
RG_IDX=$(git -C "$REPO_TOP" rev-parse --path-format=absolute --git-path index 2>/dev/null)
RG_IDX_COPIED=false
if [ -n "$RG_IDX" ] && cp -p "$RG_IDX" "$TMPIDX"; then
  RG_IDX_COPIED=true
fi
GIT_INDEX_FILE="$TMPIDX" git -C "$REPO_TOP" add -u -- . >/dev/null 2>&1
TREE_HASH=$(GIT_INDEX_FILE="$TMPIDX" git -C "$REPO_TOP" write-tree 2>/dev/null)
# The universal git empty-tree object id -- a content hash, not a per-repo
# value, so there is nothing to drift between this literal and any other
# copy of it; not worth a GC_*-style shared-constant census over.
RG_EMPTY_TREE="4b825dc642cb6eb9a060e54bf8d69288fbee4904"
RG_TREE_SUSPECT=false
if [ "$RG_IDX_COPIED" != true ] || [ "$TREE_HASH" = "$RG_EMPTY_TREE" ]; then
  RG_TREE_SUSPECT=true
fi
if [ "$RG_TREE_SUSPECT" = true ]; then echo "run-gate: WARN could not snapshot the index (${RG_IDX:-unresolved}); artifact records no tree -- merge will need an exact-sha match" >&2; TREE_HASH=""; fi

# v4.3.1 G4 -- WHICH NAME. A run before the commit exists (pre-commit-test.sh's
# Gate fallback, or a consumer's own commit-time flow) has HEAD = the PARENT,
# so two branches off one parent both wrote last-pass.<parent>.json and the
# second overwrote the first (yutraffic, 2026-09-30: PR 1's merge then found
# no artifact for its tree). When the gated tree differs from HEAD^{tree} the
# artifact is named by that tree instead; gate-before-merge.sh looks it up by
# exact tree (tier 1b) and its tree scan matches it too. A suspect capture
# (S-8) never names a file: it keeps the sha name, as before.
RG_HEAD_TREE=$(git -C "$REPO_TOP" rev-parse 'HEAD^{tree}' 2>/dev/null)
if [ "$RG_TREE_SUSPECT" != true ] && printf '%s' "$TREE_HASH" | grep -qE '^[0-9a-f]{40,64}$' \
   && [ "$TREE_HASH" != "$RG_HEAD_TREE" ]; then
  ARTIFACT="$ARTIFACT_DIR/last-pass.tree-$TREE_HASH.json"
else
  ARTIFACT="$ARTIFACT_DIR/last-pass.$HEAD_SHA.json"
fi

# v4.3.0 A2 -- ENV_HASH/ENV_DETAIL, MOVED UP from after the gate command
# exits (v4.0.3 item 13's computation) so the **Gate extra** reuse decision,
# which must be settled before anything runs, can compare against the same
# fingerprint the artifact will go on to store. v4.3.0 fix round 1, M1: this
# is a TIMING change, not a value-preserving relocation -- computing the
# environment BEFORE the gate command runs, instead of after, can genuinely
# differ if the gate command itself changes the environment (installs a
# dependency, touches server/.venv, changes the resolved node/python). FAILS
# CLOSED exactly as before: gc_gate_env returns 1 with no output when it has
# no sha256 backend, leaving ENV_HASH/ENV_DETAIL empty.
ENV_HASH=$(gc_gate_env "$REPO_TOP" 2>/dev/null) || ENV_HASH=""
ENV_DETAIL=""
[ -n "$ENV_HASH" ] && ENV_DETAIL=$(gc_gate_env "$REPO_TOP" -v 2>/dev/null | tr '\n' '|')

# v4.3.0 A2 -- **Gate extra** reuse decision (R-A and S-7 statelessness
# already validated above). Sound only when hooks/pre-commit-test.sh left a
# record for THIS EXACT working tree that ran the **Test** line itself (path
# "test" -- never "test-paths-skip" (A1) or any other pct_note label), passed
# (rc 0), used the identical **Test** command (test_sha256) and environment
# (env), agrees on the working tree (tree -- v4.3.0 fix round 1, S-6/M3: a
# belt-and-suspenders check independent of the filename lookup, so a
# corrupted or foreign record cannot be reused just because it happens to
# sit at the expected path), and is still fresh under the same 24h rule
# gate-before-merge.sh already applies to gate artifacts (GC_GATE_PRUNE_S).
# ENV_HASH empty, ENV_DETAIL carrying an `=absent` contributor, or THIS
# INVOCATION'S OWN tree capture being suspect (RG_TREE_SUSPECT, S-8) voids
# the comparison outright -- same polarity as gate-before-merge.sh's own
# tree+env TTL extension (v4.1.1 #15): a cannot-determine fingerprint must
# never sit inside a matching aggregate.
REUSED=""
RG_REC="$ARTIFACT_DIR/last-precommit.$TREE_HASH.json"
if [ -n "$GATE_EXTRA" ] && [ -f "$RG_REC" ] && [ -n "$ENV_HASH" ] && [ "$RG_TREE_SUSPECT" != true ] \
   && ! printf '%s' "$ENV_DETAIL" | grep -q '=absent'; then
  rg_rec() { grep -o "\"$1\":\"[^\"]*\"" "$RG_REC" | head -1 | sed "s/\"$1\":\"//;s/\"\$//"; }
  rg_age=$(( $(date +%s) - $(stat -c %Y "$RG_REC" 2>/dev/null || stat -f %m "$RG_REC" 2>/dev/null || echo 0) ))
  # v4.3.0 fix round 1, M3: path and rc are ANCHORED TOGETHER as one literal
  # substring -- the fixed field order (`{"path":"%s","rc":%s,...}`) makes
  # this the exact text a real "test"+rc-0 record contains, a stricter test
  # than matching the two independently.
  if grep -q '"path":"test","rc":0,' "$RG_REC" \
     && [ "$(rg_rec test_sha256)" = "$(printf '%s' "$RG_TEST" | gc_sha256)" ] \
     && [ "$(rg_rec env)" = "$ENV_HASH" ] && [ "$(rg_rec tree)" = "$TREE_HASH" ] \
     && [ "$rg_age" -le "$GC_GATE_PRUNE_S" ]; then
    REUSED=$(basename "$RG_REC")
  fi
fi

RUN_GATE_ACTIVE=1
export RUN_GATE_ACTIVE
# The provenance channel for the recursion guard at the top of this file. It
# lives inside TMPD, so the EXIT trap removes it; a nested run-gate.sh at ANY
# depth inherits the variable and touches the file before exiting 78.
#
# THE MARKER IS A FILE, AND ON WINDOWS THAT IS LOAD-BEARING (v2.2.5 round 4).
# Git Bash's MSYS layer REWRITES a POSIX-looking environment value when it
# crosses into a native Windows process: the child receives `C:/Users/.../Temp/...`
# where this script set `/tmp/...`. `mktemp -d` returns a real `/tmp/...` path on
# this platform, so the translation DOES happen in the real script — it is not a
# hypothetical. The mechanism survives it only because both spellings resolve to
# the SAME FILE and the translation is consistent in both directions. If this
# value were ever compared as a STRING — or used as a key rather than a path —
# it would break silently on Windows and nowhere else.
RUN_GATE_TERMINAL="$TMPD/terminal"
export RUN_GATE_TERMINAL
rm -f "$RUN_GATE_TERMINAL"

# v4.3.0 A2 -- run section. RUN_GATE_ACTIVE stays exported (above) around
# EVERY command this section runs, reused-Test-skip or not, exactly as
# before: a nested run-gate.sh at any depth, in any leg, must still see it.
#
# GATE_EXTRA empty: the ORIGINAL single command, byte-for-byte (the
# consistency script's registration/mirror checks anchor on this exact
# literal) -- LEGS_JSON stays "[]", untouched by anything below.
#
# GATE_EXTRA set: **Test** runs first UNLESS REUSED names a record to skip it
# (R-A already guarantees Gate == Test && Gate extra, so this is sound), then
# each **Gate extra** leg runs in argv order, stopping at the first failure.
# Per-leg results are recorded whether or not Test was reused -- R-A: "run-gate
# always runs Test and each extra leg as separate steps, so per-leg results
# exist whether or not Test is reused." A leg's own 78 is NOT given terminal
# treatment here; the single clamp below (keyed on the provenance marker, not
# on which command ran) still covers it, same as the plain-Gate path always
# has.
LEGS_JSON="[]"
if [ -z "$GATE_EXTRA" ]; then
  bash -c "$GATE_CMD"
  GATE_RC=$?
else
  GATE_RC=0
  if [ -n "$REUSED" ]; then
    echo "run-gate: Test legs reused from $REUSED"
  else
    bash -c "$RG_TEST"
    GATE_RC=$?
  fi
  if [ "$GATE_RC" -eq 0 ]; then
    # v4.3.0 fix round 1: RG_LEGS is NOT recomputed here -- it was already
    # split, once, alongside the S-7 state-free check above, and the check
    # and the run must walk the IDENTICAL list or a leg could be validated
    # against one split and executed against a different one.
    # v4.3.0 fix round 3, S-11: no `continue`-on-empty here either -- this
    # loop must never actually SEE an empty leg, because the validation
    # section above now refuses the whole **Gate extra** (clearing it, so
    # this branch is never reached at all) the moment any leg -- Test
    # included -- is empty or whitespace-only.
    # v4.3.0 fix round 5, S-13: the loop's own stdin is the here-doc holding
    # the leg list, so a leg that read stdin (`cat`, a test runner waiting on
    # input) used to swallow the remaining legs -- they never ran, GATE PASS.
    # fd 3 keeps the CALLER's stdin, and every leg reads that, exactly what
    # its counterpart in the one-command Gate reads. Closed after the loop.
    #
    # Known limits of splitting (not closed by any check above; one fresh
    # `bash -c` per leg behaves like the fresh shell the reused Test ran in):
    # (a) bash's command hash table -- a leg that installs a binary shadowing
    #     one an earlier leg already ran can make the one-command Gate reuse
    #     the old hashed path where a split leg looks it up anew;
    # (b) BASH_FUNC_* exported functions and BASH_ENV from the caller's
    #     environment are imported/sourced once per leg when split, once in
    #     the plain Gate.
    exec 3<&0
    RG_LEGS_ARR=""
    while IFS= read -r rg_leg; do
      rg_t0=$(date +%s 2>/dev/null || echo 0)
      bash -c "$rg_leg" <&3 3<&-
      rg_leg_rc=$?
      rg_t1=$(date +%s 2>/dev/null || echo 0)
      rg_leg_sha=$(printf '%s' "$rg_leg" | gc_sha256 2>/dev/null)
      RG_LEGS_ARR="${RG_LEGS_ARR:+$RG_LEGS_ARR,}{\"sha256\":\"$rg_leg_sha\",\"rc\":$rg_leg_rc,\"elapsed_s\":$((rg_t1 - rg_t0))}"
      if [ "$rg_leg_rc" -ne 0 ]; then
        GATE_RC=$rg_leg_rc
        break
      fi
    done <<RG_LEG_LIST
$RG_LEGS
RG_LEG_LIST
    exec 3<&-
    LEGS_JSON="[$RG_LEGS_ARR]"
  fi
fi

# THE CLAMP. NOT DEAD CODE — DELETING IT OPENS A COLLISION CHANNEL (v2.2.5
# round 3). Until this release every nonzero from the gate command collapsed to
# a hardcoded `exit 1`, because `$?` was never captured. That accidental clamp is
# what kept the toolchain safe, and giving the guard a distinguishable code is
# exactly the change that leads someone to refactor it into `exit $GATE_RC` —
# at which point a consumer gate command exiting 78 for its own reason (78 is
# EX_CONFIG; real programs emit it) inherits the terminal remedy text "edit your
# **Gate** value", printed over a plain test failure. That is INVERTED advice,
# strictly worse than the generic retry line it replaces. Measured downstream:
# `uv run` propagates a child's code verbatim, so the channel is open one layer
# up and closed only here.
#
# So a gate command's 78 is clamped to 1 — UNLESS a nested run-gate.sh left the
# provenance marker, which is the one case where the 78 really is this script's
# own terminal guard talking. Keyed on WHO decided, not on the number.
#
# THE HONEST LIMIT OF THE MARKER (v2.2.5 round 4). It proves that *a* nested
# run-gate.sh exited terminally during THIS invocation. It does NOT prove that
# *this* `$GATE_RC` came from that nested run. A gate of the form
# `bash hooks/run-gate.sh; some-other-tool` sets the marker via the recursion
# guard and then takes its final rc from the second command — so an unrelated 78
# there inherits the terminal remedy, which is the very collision this clamp
# closes, reopened one step along. It needs a self-referencing gate AND a second
# command exiting 78, and closing it would mean reconstructing the causal chain
# rather than a single fact, so it is recorded as a known edge rather than
# fixed. Read this test as "a terminal guard fired in here", not as
# "provenance settled".
if [ "$GATE_RC" -eq "$GC_TERMINAL_RC" ] && [ ! -f "$RUN_GATE_TERMINAL" ]; then
  GATE_RC=1
fi

# v2.4.0 (A6, observed live and unplanned during v2.3.0's release): THE
# CHECKOUT CAN MOVE UNDER A RUNNING GATE. Two gate runs overlapped; the second
# was still running when the checkout moved from detached c43f51f to `main`. It
# finished green and wrote `sha: c43f51f` — a sha captured at one moment,
# describing a run whose working tree changed midway through it. No harm came of
# it only because the two trees happened to be byte-identical, which is luck,
# not a property. (Both runs also recorded `"branch":"unknown"` because the
# checkout was detached, so the `branch` field cannot be relied on either.)
#
# HEAD_SHA and TREE_HASH above were both captured BEFORE the gate command ran.
# Re-read HEAD now: if it moved, the run described no single coherent state and
# the artifact would be a receipt for something that never existed. Refuse to
# write it, delete any older one, and say why. Exit 1 rather than the terminal
# 78 — a concurrent checkout move is a race, not a settled condition, so
# "re-run it" is the right advice.
if [ "$GATE_RC" -eq 0 ]; then
  HEAD_SHA_AFTER=$(git -C "$REPO_TOP" rev-parse HEAD 2>/dev/null)
  if [ "$HEAD_SHA_AFTER" != "$HEAD_SHA" ]; then
    rm -f "$ARTIFACT" "$ARTIFACT_DIR/last-pass.$HEAD_SHA.json" "$ARTIFACT_DIR/last-pass.tree-$TREE_HASH.json"
    echo "GATE ERROR: the checkout moved while the gate was running (HEAD was ${HEAD_SHA:-unknown} at start, is ${HEAD_SHA_AFTER:-unknown} now)." >&2
    echo "The run does not describe any single state, so no artifact was written. Settle the checkout and re-run 'bash hooks/run-gate.sh'." >&2
    exit 1
  fi
  mkdir -p "$ARTIFACT_DIR"
  TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # Atomic write (v4.0.1 addendum to item 17): the shared directory now has
  # readers from OTHER processes -- a concurrent gate-before-merge.sh in
  # another worktree scanning this directory for a tree match -- so a partial
  # write must never be observable. Write to a sibling .tmp file and `mv` it
  # into place: `mv` is atomic within one filesystem, and the tmp file always
  # lands in the artifact's own directory. Readers (gate-before-merge.sh's
  # exact lookup and its tree scan) both skip `*.tmp` for this reason.
  # v4.0.3 item 13 -- the environment fingerprint gate-before-merge.sh's
  # tree+env TTL extension keys on (see gc_gate_env's header note above).
  # ENV_HASH is what "env" stores (the aggregate the extension compares).
  # ENV_DETAIL is an INTERFACE ADDITION beyond the original item 13 spec text
  # (which named only the "env" key): the per-contributor labelled lines
  # (gc_gate_env -v), pipe-joined so they fit one JSON string field, needed
  # so gate-before-merge.sh can NAME which contributor changed
  # ("environment changed: pyvenv") -- a single aggregate hash cannot say
  # that on its own, since only the CURRENT contributors are recomputable at
  # merge time; the OLD per-contributor values have to have been stored
  # somewhere. Reported in the task report as a deviation from the brief's
  # "artifact key env" (singular) wording.
  # FAILS CLOSED (reviewer): gc_gate_env returns 1 with no output when it has
  # no sha256 backend -- ENV_HASH/ENV_DETAIL then stay empty, so an artifact
  # minted where the fingerprint could not be computed carries no `env` at
  # all, which gate-before-merge.sh's extension already treats as "not
  # eligible for the extension" (same as an older writer's artifact).
  # v4.3.0 A2: ENV_HASH/ENV_DETAIL are no longer (re)computed here -- they were
  # moved BEFORE the run (above, alongside TREE_HASH) so the **Gate extra**
  # reuse decision could use them; the values stored below are identical to
  # what that earlier computation produced, per the CAVEAT two paragraphs up
  # (both are taken before the gate command runs).
  # `reused_test` (v4.3.0 A2): the reused record's filename, or "" when Test
  # ran (GATE_EXTRA unset/invalid, or no eligible record). `legs` (A2): one
  # entry per **Gate extra** leg actually run this invocation -- "[]" when
  # GATE_EXTRA is not in effect.
  ARTIFACT_TMP="$ARTIFACT.tmp"
  printf '{"sha":"%s","tree":"%s","branch":"%s","ts":"%s","status":"pass","env":"%s","env_detail":"%s","reused_test":"%s","legs":%s}\n' \
    "$HEAD_SHA" "$TREE_HASH" "${BRANCH:-unknown}" "$TS" "$ENV_HASH" "$ENV_DETAIL" "$REUSED" "$LEGS_JSON" > "$ARTIFACT_TMP"
  mv -f "$ARTIFACT_TMP" "$ARTIFACT"
  echo "GATE PASS $HEAD_SHA"
  # Prune (v4.0.1 addendum to item 17): the directory is shared across every
  # worktree and never swept by a commit (it lives inside .git), so without
  # this it grows one file per gate run forever. The prune window is
  # GC_GATE_PRUNE_S (v4.0.3, R4 -- ONE expression derived from GC_GATE_TTL_S,
  # defined once in hooks/lib/git-cmd.sh and repeated here for the same
  # standalone reason as GC_GATE_TTL_S/gc_gate_dir): always 24x the freshness
  # window gate-before-merge.sh enforces, so pruning can never delete an
  # artifact a merge would still honour. That relation is structural, not
  # asserted with a runtime self-check -- a self-check that can never go red
  # for any positive GC_GATE_TTL_S is not a check; if the derivation is ever
  # replaced with an independent constant, add a real one.
  prune_min=$(( GC_GATE_PRUNE_S / 60 ))
  find "$ARTIFACT_DIR" -maxdepth 1 -name 'last-pass.*.json' -mmin "+$prune_min" -delete 2>/dev/null || true
  exit 0
elif [ "$GATE_RC" -eq "$GC_TERMINAL_RC" ]; then
  # TERMINAL: reachable only when the clamp above let the 78 through, i.e.
  # something left the provenance marker. Two producers, one rule:
  #   * a NESTED run-gate.sh hitting its own recursion guard (a self-invoking
  #     **Gate**, directly or through a wrapper);
  #   * since v2.3.0, THE **Gate** COMMAND ITSELF, following the public contract
  #     in docs/verification.md (print remedy, touch $RUN_GATE_TERMINAL, exit
  #     78). The marker never meant "run-gate.sh decided"; it means "whoever
  #     exited took responsibility for the remedy", which is why the clamp is
  #     keyed on it and not on the caller.
  # DELIBERATELY SILENT. The generic "fix the failures and re-run" of the else
  # arm is wrong here, and so is any replacement of it: only the guard knows the
  # specific remedy, it has already printed it on this same stderr, and it must
  # stay the LAST thing on screen. Printing a trailing summary would bury it
  # again — which is the exact defect this branch exists to fix. The code is
  # propagated so the caller (pre-commit-test.sh) can suppress ITS retry advice
  # by the same structural test, without knowing which guard fired.
  rm -f "$ARTIFACT" "$ARTIFACT_DIR/last-pass.$HEAD_SHA.json" "$ARTIFACT_DIR/last-pass.tree-$TREE_HASH.json"
  exit "$GC_TERMINAL_RC"
else
  rm -f "$ARTIFACT" "$ARTIFACT_DIR/last-pass.$HEAD_SHA.json" "$ARTIFACT_DIR/last-pass.tree-$TREE_HASH.json"
  echo "GATE FAILED: '$GATE_CMD' exited nonzero. Fix the failures and re-run 'bash hooks/run-gate.sh'." >&2
  exit 1
fi
