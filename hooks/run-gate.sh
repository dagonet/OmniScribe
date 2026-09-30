#!/usr/bin/env bash
# Gate runner (invoked by developers/PO, not registered as a hook):
#   bash hooks/run-gate.sh
#
# Reads the Gate command from PROJECT_CONTEXT.md ("**Gate**: <command>",
# with or without a leading list marker) and runs it. On success, writes
# the gate artifact that hooks/gate-before-merge.sh checks before allowing
# a PR merge:
#
#   <common git dir>/gate/last-pass.<HEAD sha>.json  (v4.0.1, item 17 -- see
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
  echo "Green: writes <common git dir>/gate/last-pass.<sha>.json (checked by gate-before-merge.sh) and prints GATE PASS <sha>."
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
# artifact. See the header note for the full rationale.
ARTIFACT="$ARTIFACT_DIR/last-pass.$HEAD_SHA.json"

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
cp "$(git -C "$REPO_TOP" rev-parse --git-path index)" "$TMPIDX" 2>/dev/null || true
GIT_INDEX_FILE="$TMPIDX" git -C "$REPO_TOP" add -u -- . >/dev/null 2>&1
TREE_HASH=$(GIT_INDEX_FILE="$TMPIDX" git -C "$REPO_TOP" write-tree 2>/dev/null)

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
bash -c "$GATE_CMD"
GATE_RC=$?

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
    rm -f "$ARTIFACT"
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
  ENV_HASH=$(gc_gate_env "$REPO_TOP" 2>/dev/null) || ENV_HASH=""
  ENV_DETAIL=""
  [ -n "$ENV_HASH" ] && ENV_DETAIL=$(gc_gate_env "$REPO_TOP" -v 2>/dev/null | tr '\n' '|')
  ARTIFACT_TMP="$ARTIFACT.tmp"
  printf '{"sha":"%s","tree":"%s","branch":"%s","ts":"%s","status":"pass","env":"%s","env_detail":"%s"}\n' \
    "$HEAD_SHA" "$TREE_HASH" "${BRANCH:-unknown}" "$TS" "$ENV_HASH" "$ENV_DETAIL" > "$ARTIFACT_TMP"
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
  rm -f "$ARTIFACT"
  exit "$GC_TERMINAL_RC"
else
  rm -f "$ARTIFACT"
  echo "GATE FAILED: '$GATE_CMD' exited nonzero. Fix the failures and re-run 'bash hooks/run-gate.sh'." >&2
  exit 1
fi
