#!/usr/bin/env bash
# PreToolUse hook: require passing tests before git commit
# Matcher: Bash|PowerShell
#
# Runs the project's test command before allowing a commit.
# Blocks the commit if tests fail.
#
# No-op when TEST_COMMAND is still a placeholder (template not configured)
# or when PROJECT_CONTEXT.md doesn't exist.
#
# Buggy code was the #1 friction category (10 occurrences) in Insights report.
# This hook prevents shipping code that breaks existing tests.
#
# v2.0: the native git CLI is allowed again, so this gate parses
# tool_input.command instead of keying on the retired mcp__git-tools__git_commit
# tool name. Escape hatch: <cwd>/.claude/git-guard-off.
#
# v2.2.0: the payload is parsed through hooks/lib/json.sh (node, python3 or jq).
# With none of the three on PATH this gate fails CLOSED (exit 2) like the other
# two git gates — a commit whose command cannot be read is not a commit that can
# be shown to have passed its tests. `git-guard-off` still opts out.
#
# READING THE OUTPUT (v2.2.1, from a consumer report):
#   - A green run prints `passed. (<n>s)` and NOTHING else. The captured output
#     is deleted on success by design, so "I saw no test output" is not evidence
#     the tests did not run — the ELAPSED SECONDS are. A real suite takes
#     minutes (616 s measured on a three-project repo); a hook that fell through
#     its own guards returns in about a second.
#   - A `**Test**` value chaining projects with `&&` SHORT-CIRCUITS. When the
#     first project fails, the later ones are UNRUN — not passing. The block
#     message names the whole command, so do not read a failure as "everything
#     after the first project was fine"; nothing after it was executed at all.

trap '[ "$?" = 127 ] && exit 2' EXIT   # v4.4.0 C2: the old registration wrapper's 127->2, now in-hook (exec/source forms cannot wrap)

# v4.3.1 T1-5: bash imports SECONDS from the environment; reset it so the hook-wide ceiling counts from THIS hook's start.
SECONDS=0

# Fail CLOSED when the sourced lib is missing: without it every gc_* helper is
# undefined, GC_CMD stays empty, and this gate would exit 0 on every commit.
lib="$(dirname "$0")/lib/git-cmd.sh"
[ -f "$lib" ] || { echo "BLOCKED: $lib missing — run /sync-template step 6b (hooks/lib/git-cmd.sh)" >&2; exit 2; }
. "$lib"
command -v gc_current_branch >/dev/null 2>&1 || { echo "BLOCKED: $lib is present but corrupt (gc_current_branch undefined) — this gate cannot evaluate the command, refusing" >&2; exit 2; }

# v2.1.3 fix round 2: absolutize RUN_GATE HERE, before any `cd`. $0 is a
# relative path when the harness invokes `bash hooks/pre-commit-test.sh`, and
# a relative "$(dirname "$0")/run-gate.sh" is not re-resolved until it is
# actually used below -- by then the script has `cd`'d into REPO_PATH (which
# `git -C <other-repo> commit` can point anywhere), so the stale relative path
# would resolve against the WRONG repo: silently missing there (masking an
# intended run-gate.sh as the legacy eval path), or worse, hitting that other
# repo's own hooks/run-gate.sh instead of this toolkit's.
RUN_GATE="$(cd "$(dirname "$0")" && pwd)/run-gate.sh"

# v3.0.3 — THE SIDE EFFECT THAT OUTLIVES THE HOOK (last-precommit.json under
# <common git dir>/gate/, v4.0.1 item 17 — see gc_gate_dir's header note in
# hooks/lib/git-cmd.sh).
#
# A PreToolUse hook completes BEFORE the tool it gates ever starts, and the
# harness drops non-blocking hook stderr. Between them, nothing this hook PRINTS
# can place it relative to the command it gated: a commit that "returns
# instantly" with no visible output is indistinguishable, from outside, from a
# hook that never ran. A consumer spent twenty minutes with a hand-driven
# payload proving that a ten-minute hook HAD run. This file answers that in one
# read, and it is why the answer is an artifact and not a message.
#
# IT IS A DIAGNOSTIC AND NEVER A GATE. Every failure below is swallowed —
# unwritable cwd, no git repo, a read-only gate directory, a git older than
# 2.31 (gc_gate_dir's own fallback WARN is swallowed here too — a diagnostic
# path must never grow new stderr of its own, v4.0.1 addendum; the one
# exception: the index `cp` error below is deliberately visible since v4.4.0,
# to diagnose the empty-tree case). A diagnostic
# that can block a commit is a second gate nobody declared, and it would be
# the worst kind: one whose refusal has nothing to do with the tests.
#
# Written on every path past payload parsing, so the file distinguishes "the
# hook ran and found nothing to gate" from "the hook did not run". Not written
# on the two exits BEFORE the payload is understood — the guard-off kill switch
# (whose whole contract is that this hook does nothing) and the pre-v2
# settings.json refusal (which has no readable command to describe).
#
# WHERE THE ARTIFACT LANDS. Written under `<common git dir>/gate/` (v4.0.1,
# item 17) resolved from the repo whose commit was gated, which is NOT the cwd
# when `-C` is in play. Exception: a `global-refused` artifact resolves
# against the cwd repo, because that refusal fires before the target is
# resolved (PCT_ARTIFACT_BASE is assigned after REPO_PATH). Reading the target
# repo's gate directory after such a refusal finds nothing there, which is not
# evidence the hook did not run. The filename carries the gated TREE (v4.0.1
# addendum), not a fixed name: `last-precommit.<tree>.json` and
# `last-precommit-noop.<tree>.json`, `<tree>` replaced with the literal
# `unknown` when no tree was hashed (unreadable, empty-cmd, global-refused,
# unresolved-c — every path before pct_capture_tree can run).
# v4.3.2 F2 -- the clock without a fork where bash can: printf %(...)T is a
# builtin from bash 4.2 (Git Bash, Linux); older bash (macOS /bin/bash 3.2)
# keeps `date`. A caller wanting UTC prefixes the call with TZ=UTC0.
pct_now() { # <var> <strftime format>
  if [ "${BASH_VERSINFO[0]:-0}" -gt 4 ] || { [ "${BASH_VERSINFO[0]:-0}" -eq 4 ] && [ "${BASH_VERSINFO[1]:-0}" -ge 2 ]; }; then
    printf -v "$1" "%($2)T" -1
  else
    printf -v "$1" '%s' "$(date "+$2" 2>/dev/null)"
  fi
}
pct_now PCT_HOOK_T0 '%s'
[ -n "$PCT_HOOK_T0" ] || PCT_HOOK_T0=0
PCT_ARTIFACT_BASE=""
PCT_TREE=""
# v4.3.0 fix round 1, S-8 (I1) -- true when pct_capture_tree's own index copy
# could not be trusted (the real index was not actually copied, or the
# resulting tree is the universal git EMPTY TREE) -- see that function.
# pct_note reads this to withhold test_sha256/env (a reusable-record field)
# rather than let a spurious empty-tree measurement enter a reuse decision.
PCT_TREE_SUSPECT=false
# v3.1 — whether the commit segment this hook matched came from inside an
# unwrapped quoted payload (`bash -c "git commit ..."`, `sh -lc "..."`). Set
# once a commit segment is found (below); false until then, so every path that
# writes last-precommit.json before a commit segment is known (unreadable,
# empty-cmd) reports it honestly as false.
PCT_QUOTED=false

# v3.0.3 — WHICH TREE THIS HOOK GATED. Two consumers hit the same symptom in one
# evening from opposite causes: a green commit, an artifact the merge gate calls
# stale, and nothing printed. One had batched `cat addendum >> FILE; git add;
# git commit` into a SINGLE Bash call — a PreToolUse hook hashes the working
# tree BEFORE the call runs, so the append happened after the gate; the other had
# an untracked message file swept into the gated tree by `add -A` and absent from
# the commit. From outside those read identically. With this field they are one
# comparison apart: artifact tree == the PARENT's tree means the mutation was
# batched with the commit; equal to neither means an untracked file moved.
#
# Computed EXACTLY as run-gate.sh computes `tree` for last-pass.<sha>.json
# (temp index, add -u -- ., write-tree — hooks/run-gate.sh) so the two
# artifacts cannot disagree about what "tree" names. v3.1: tracked files
# only -- an untracked file no longer enters either hash. Captured BEFORE the
# Test command or run-gate.sh runs: that is the state the verdict describes.
pct_capture_tree() {
  [ -n "$PCT_ARTIFACT_BASE" ] || return 0
  _pt_top=$(git -C "$PCT_ARTIFACT_BASE" rev-parse --show-toplevel 2>/dev/null) || return 0
  [ -n "$_pt_top" ] || return 0
  _pt_d=$(mktemp -d 2>/dev/null) || return 0
  # `--git-path index`, never a hardcoded .git/index: in a linked worktree the
  # index lives under .git/worktrees/<name>/.
  # v4.3.0 fix round 1, S-8 (I1/I2). `--path-format=absolute`: the BARE
  # `--git-path` prints a path RELATIVE TO THE CALLING PROCESS'S OWN cwd in a
  # plain (non-worktree) checkout -- from a cwd other than $_pt_top (e.g. this
  # hook invoked with `-C` a subdirectory, or from inside one) that relative
  # path resolves to a nonexistent file, `cp` used to fail SILENTLY, and the
  # subsequent `add -u` on a freshly-created EMPTY index does nothing at all,
  # producing the git EMPTY TREE instead of a real measurement. `-p`
  # preserves the REAL index file's timestamps on the copy rather than
  # stamping "now" -- without it, git's own racy-git protection can misjudge
  # a same-second edit as already reflected in the copy (measured stale 7 of
  # 8; matches hooks/run-gate.sh's own identical fix).
  _pt_idx=$(git -C "$_pt_top" rev-parse --path-format=absolute --git-path index 2>/dev/null)
  _pt_copied=false
  if [ -n "$_pt_idx" ] && cp -p "$_pt_idx" "$_pt_d/index"; then
    _pt_copied=true
  fi
  GIT_INDEX_FILE="$_pt_d/index" git -C "$_pt_top" add -u -- . >/dev/null 2>&1
  PCT_TREE=$(GIT_INDEX_FILE="$_pt_d/index" git -C "$_pt_top" write-tree 2>/dev/null)
  # Universal git empty-tree object id -- a content hash, not a per-repo
  # value, so there is nothing to drift between this literal and the copy in
  # hooks/run-gate.sh.
  if [ "$_pt_copied" != true ] || [ "$PCT_TREE" = "4b825dc642cb6eb9a060e54bf8d69288fbee4904" ]; then
    PCT_TREE_SUSPECT=true
  fi
  rm -rf "$_pt_d"
  return 0
}
# v4.3.2 F2 -- THE NO-OP RECORD, CHEAPLY. Same file, same keys, same meaning
# as before; pct_note delegates here for no-commit-segment. Three programs on
# the fast path instead of ~12: ONE git call for top-level and common dir
# (pct_note's own rev-parse plus gc_gate_dir's two made three), the builtin
# clock and byte count instead of date/wc/tr, mkdir only when missing, and no
# prune -- this file is always last-precommit-noop.unknown.json (PCT_TREE is
# set only once a commit segment is found), so it never accumulates, and the
# commit paths' pct_prune still removes no-op files older versions left. The
# one-call answer is used only when it is two absolute lines (plan R-6); any
# other answer (git < 2.31, no repository) takes gc_gate_dir as before. Still
# a diagnostic: every failure returns 0 and nothing reaches stderr.
pct_note_noop() { # <rc>
  local LC_ALL=C _pn_base _pn_rp _pn_cd _pn_gd="" _pn_t1 _pn_ts _pn_tool _pn_noop
  _pn_base="${PCT_ARTIFACT_BASE:-$GC_CWD}"
  [ -n "$_pn_base" ] || return 0
  _pn_rp=$(git -C "$_pn_base" rev-parse --path-format=absolute --show-toplevel --git-common-dir 2>/dev/null) || _pn_rp=""
  _pn_cd=${_pn_rp#*"$GC_NL"}
  case "$_pn_rp" in
    /*"$GC_NL"/*|[A-Za-z]:*"$GC_NL"[A-Za-z]:*)
      case "$_pn_cd" in *"$GC_NL"*) ;; *) _pn_gd="$_pn_cd/gate" ;; esac ;;
  esac
  [ -n "$_pn_gd" ] || _pn_gd=$(gc_gate_dir "$_pn_base" 2>/dev/null)
  [ -n "$_pn_gd" ] || return 0
  [ -d "$_pn_gd" ] || mkdir -p "$_pn_gd" 2>/dev/null || return 0
  pct_now _pn_t1 '%s'
  TZ=UTC0 pct_now _pn_ts '%Y-%m-%dT%H:%M:%SZ'
  case "${GC_TOOL:-}" in
    Bash)       _pn_tool=Bash ;;
    PowerShell) _pn_tool=PowerShell ;;
    *)          _pn_tool=other ;;
  esac
  _pn_noop="$_pn_gd/last-precommit-noop.${PCT_TREE:-unknown}.json"
  # cmd_len: ${#} under LC_ALL=C is BYTES (the v4.1.2 contract wc -c kept).
  printf '{"path":"%s","rc":%s,"tree":"%s","elapsed_s":%s,"cmd_len":%s,"tool":"%s","ts":"%s","kind":"no-commit-segment","gate_dir":"%s"}\n' \
    no-commit-segment "$1" "$PCT_TREE" "$(( ${_pn_t1:-0} - PCT_HOOK_T0 ))" "${#GC_CMD}" "$_pn_tool" "$_pn_ts" "$_pn_gd" \
    > "$_pn_noop.tmp" 2>/dev/null && mv -f "$_pn_noop.tmp" "$_pn_noop" 2>/dev/null
  return 0
}
pct_note() { # <path-label> <rc, or -1 where no subshell ran>
  # v4.3.2 F2: the no-op record has its own cheap writer (pct_note_noop).
  if [ "$1" = no-commit-segment ]; then pct_note_noop "$2"; return 0; fi
  _pn_base="${PCT_ARTIFACT_BASE:-$GC_CWD}"
  [ -n "$_pn_base" ] || return 0
  # "At the repo top" has a precondition. Outside a repo there is no top to
  # write to, and creating .gate/ in an arbitrary cwd would litter — this hook
  # sees every Bash call, not only commits.
  _pn_top=$(git -C "$_pn_base" rev-parse --show-toplevel 2>/dev/null) || return 0
  [ -n "$_pn_top" ] || return 0
  # v4.0.1 (item 17): the shared <common git dir>/gate/ directory, not a
  # per-worktree .gate/ at toplevel -- see gc_gate_dir's header note in
  # hooks/lib/git-cmd.sh. `2>/dev/null` swallows its git<2.31 fallback WARN:
  # this function is a diagnostic that must never grow stderr of its own.
  _pn_gd=$(gc_gate_dir "$_pn_top" 2>/dev/null)
  [ -n "$_pn_gd" ] || return 0
  mkdir -p "$_pn_gd" 2>/dev/null || return 0
  # v4.0.1 (item 17 addendum): the filename carries the gated TREE, not a
  # fixed name -- the directory is shared by every worktree now, same reason
  # as last-pass.<sha>.json in run-gate.sh. "unknown" on every path that
  # exits before pct_capture_tree can run (unreadable, empty-cmd,
  # global-refused, unresolved-c).
  _pn_treeseg="${PCT_TREE:-unknown}"
  _pn_t1=$(date +%s 2>/dev/null || echo 0)
  # `tool` is the ONLY payload-controlled field in this record. Mapped to the
  # declared enum rather than interpolated: a tool_name carrying a quote or a
  # backslash would otherwise produce malformed JSON in exactly the file
  # somebody reads when they are already confused about what ran.
  case "${GC_TOOL:-}" in
    Bash)       _pn_tool=Bash ;;
    PowerShell) _pn_tool=PowerShell ;;
    *)          _pn_tool=other ;;
  esac
  # v3.1 — SPLIT ARTIFACT (three-consumer measurement: inspecting the artifact
  # is itself what destroys it). Before this, a plain `ls` run moments after a
  # commit overwrote that commit's OWN last-precommit.json record with
  # path=no-commit-segment, because pct_note wrote every path — commit or not —
  # to the same file: a consumer who read the artifact a call too late saw "the
  # hook never ran" for a hook that, in fact, had. `no-commit-segment` now lands
  # in its OWN file, last-precommit-noop.json, which nothing else ever writes to
  # — so it can never clobber a commit's record — and last-precommit.json is
  # left untouched on that path. Every other path (including empty-cmd and
  # unreadable, which also found no commit but for a different reason) keeps
  # writing last-precommit.json exactly as before, now carrying
  # matched_in_quoted as well. (v4.3.2: written by pct_note_noop.)
  # The COMMAND ITSELF is never recorded, only its length: this file lands in
  # the consumer's repo, and a diagnostic is not a place to accumulate command
  # history. printf, so no jq is required on the path that reports jq missing.
  # `tree` is "" on every path where nothing was hashed because nothing ran.
  # `matched_in_quoted` (v3.1): true when the commit segment this hook matched
  # was only found because gc_segments strips quote characters -- a payload of
  # the shape `bash -c "git commit -m x"` -- as opposed to an unwrapped `git
  # commit -m x`; see gc_seg_quoted in hooks/lib/git-cmd.sh.
  _pn_art="$_pn_gd/last-precommit.$_pn_treeseg.json"
  # v4.3.0 A2 -- test_sha256/env, on THIS file only (the -noop file above is
  # unrelated and unchanged). Populated ONLY when this call reports a real,
  # PASSING **Test** run ($1=test, rc=0): a skip (test-paths-skip, A1), a
  # failure, the Gate fallback (labelled "gate"), or any other pct_note label
  # must never look like a reusable Test record to hooks/run-gate.sh, whose
  # own reuse check (R-A, spec Part A2) requires path=="test" AND rc==0 before
  # it even reads these two fields -- storing them elsewhere would create a
  # record that COULD spuriously satisfy that requirement if a future edit
  # ever relaxed it, which is a trap this file declines to lay.
  _pn_tsha=""
  _pn_env=""
  if [ "$1" = test ] && [ "$2" = 0 ] && [ "$PCT_TREE_SUSPECT" != true ]; then
    # v4.3.0 fix round 1, S-6 (C1 -- "wrong reuse across directories"). These
    # two fields must describe the TOPLEVEL's own **Test**, never a
    # subdirectory's. A commit issued from cwd sub/ (a linked worktree, or a
    # plain checkout's own subdirectory) resolves REPO_PATH -- and so
    # PCT_ARTIFACT_BASE/`$_pn_base` -- to sub/, reads sub/PROJECT_CONTEXT.md,
    # and runs sub/t.sh; but hooks/run-gate.sh always reads **Test** and
    # computes its environment fingerprint from the TOPLEVEL. If sub/'s
    # **Test** text happens to be byte-identical to the toplevel's own (an
    # entirely plausible coincidence, not an attack -- MEASURED: reused,
    # GATE PASS, and the failing top-level Test never ran), the two would
    # otherwise "match" while describing two different scripts. `pwd -P`
    # (physical, symlink-resolved) rather than a plain string compare of
    # `$_pn_base` vs `$_pn_top`: a bind mount or a symlinked checkout could
    # make the TEXT of the two paths differ while the DIRECTORY is the same
    # one, or vice versa -- physical identity is the actual question.
    # v4.3.1 G6: REPO_PATH is now always the top-level, so this comparison holds
    # for every commit that reaches a Test; it stays as the guard for any future
    # path that sets PCT_ARTIFACT_BASE elsewhere.
    _pn_base_phys=$(cd "$_pn_base" 2>/dev/null && pwd -P)
    _pn_top_phys=$(cd "$_pn_top" 2>/dev/null && pwd -P)
    if [ -n "$_pn_base_phys" ] && [ "$_pn_base_phys" = "$_pn_top_phys" ]; then
      _pn_tsha=$(printf '%s' "$TEST_CMD" | gc_sha256 2>/dev/null)
      # The environment fingerprint is computed from `$_pn_top` (the same
      # name hooks/run-gate.sh's own REPO_TOP resolves to), never from
      # `$PCT_ARTIFACT_BASE` -- this check just proved the two are
      # physically identical, but `$_pn_top` is the name the rest of this
      # function already uses for that toplevel.
      _pn_env=$(gc_gate_env "$_pn_top" 2>/dev/null)
    fi
  fi
  printf '{"path":"%s","rc":%s,"tree":"%s","elapsed_s":%s,"cmd_len":%s,"tool":"%s","ts":"%s","matched_in_quoted":%s,"gate_dir":"%s","test_sha256":"%s","env":"%s"}\n' \
    "$1" "$2" "$PCT_TREE" "$((_pn_t1 - PCT_HOOK_T0))" "$(printf '%s' "$GC_CMD" | wc -c | tr -d ' ')" "$_pn_tool" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" "$PCT_QUOTED" "$_pn_gd" "$_pn_tsha" "$_pn_env" \
    > "$_pn_art.tmp" 2>/dev/null && mv -f "$_pn_art.tmp" "$_pn_art" 2>/dev/null || return 0
  pct_prune "$_pn_gd"
  return 0
}

# pct_prune <gate_dir> -- v4.0.3 item 4: last-precommit.<tree>.json and
# last-precommit-noop.<tree>.json grew one file per tree forever (unlike
# run-gate.sh's last-pass.*.json, which has pruned itself since v4.0.1).
# Same derived window as that prune -- GC_GATE_PRUNE_S (hooks/lib/git-cmd.sh:
# ONE expression, never a second literal) -- applied here on the WRITE path
# only, right after each artifact is placed: a READ path (this hook has none
# that inspects these files, but the discipline is stated so nobody adds
# one) must never have side effects. `2>/dev/null || true`: this is a
# diagnostic path, and must never grow stderr of its own (same rule pct_note
# itself follows throughout).
pct_prune() {
  [ -n "$1" ] || return 0
  _pp_min=$(( GC_GATE_PRUNE_S / 60 ))
  find "$1" -maxdepth 1 \( -name 'last-precommit.*.json' -o -name 'last-precommit-noop.*.json' \) -mmin "+$_pp_min" -delete 2>/dev/null || true
}

# v4.3.1 G1 -- THE PER-COMMIT RUN HAS A BUDGET BELOW THE HARNESS HOOK TIMEOUT.
# MM-Agent, 2026-09-30: a 641 s and a 1981 s Test outlived the harness's 600 s
# hook timeout; the harness killed THIS hook, treated that as a NON-blocking
# error and let the commit through, while the Test's children ran on as
# orphans. The run now happens in a child this hook owns, in its own process
# group (set -m), and stops at the budget: every member's native Windows
# subtree dies first (taskkill //T on /proc/<pid>/winpid -- measured
# 2026-10-02: taskkill on the leader's winpid ALONE leaves cygwin and native
# grandchildren running, because a cygwin exec breaks the Windows parent
# chain), then the group itself; the commit is REFUSED (exit 2). Every
# registration carries "timeout": PCT_TIMEOUT_MAX + 60 (consistency check 64),
# so this budget fires before the harness does -- PROVIDED the work around the
# run fits in the 60 s margin, which a loaded machine can break (measured: one
# running gate slows every spawn 10-30x). Hence the hook-wide ceiling below.
PCT_TIMEOUT_DEFAULT=540
PCT_TIMEOUT_MIN=30
PCT_TIMEOUT_MAX=3300
# v4.3.1 T1-1: the per-run budget counts from the fork; the harness counts from
# hook start. The run therefore ALSO stops when bash $SECONDS (hook start)
# reaches the registration timeout (PCT_TIMEOUT_MAX + 60) minus
# PCT_CEIL_RESERVE, whichever limit comes first. 45 s is what the kill path
# needs after the ceiling trips: up to 5 kill rounds (one taskkill spawn per
# group member each, 1 s sleeps) plus the record, prune and refusal text, at
# spawn latencies several times the idle ones; the remaining 15 s of the margin
# absorb poll granularity.
PCT_CEIL_RESERVE=45

# pct_ceiling -- the hook-wide ceiling in seconds since hook start.
# PCT_TEST_CEILING_TESTONLY_S is for the fixtures only and can only LOWER it
# (a whole number below the real ceiling), never raise it past the harness timeout.
pct_ceiling() {
  _pc_c=$((PCT_TIMEOUT_MAX + 60 - PCT_CEIL_RESERVE))
  case "${PCT_TEST_CEILING_TESTONLY_S:-}" in
    ''|*[!0-9]*) ;;
    *) [ "${#PCT_TEST_CEILING_TESTONLY_S}" -le 4 ] && [ "$PCT_TEST_CEILING_TESTONLY_S" -ge 1 ] && [ "$PCT_TEST_CEILING_TESTONLY_S" -lt "$_pc_c" ] && _pc_c=$PCT_TEST_CEILING_TESTONLY_S ;;
  esac
  printf '%s\n' "$_pc_c"
}

# pct_budget <repo> -- the budget in seconds: **Test timeout** when it is a
# whole number in PCT_TIMEOUT_MIN..PCT_TIMEOUT_MAX, else (WARN) the default.
# Unset, empty or an unfilled {{...}} placeholder is the default, silently.
# PCT_TEST_TIMEOUT_TESTONLY_S is for the fixtures only and can only SHORTEN the
# budget (1-29 s, below the configurable range): a larger value would let the
# harness kill the hook first, which is the fail-open this budget closes.
pct_budget() {
  _pb_v=$(grep -E "${GC_KEY_PRE}\*\*Test timeout\*\*:" "$1/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Test timeout\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)
  case "$_pb_v" in *\{\{*\}\}*) _pb_v="" ;; esac
  _pb_b=$PCT_TIMEOUT_DEFAULT
  if [ -n "$_pb_v" ]; then
    case "$_pb_v" in
      *[!0-9]*)
        echo "pre-commit-test: WARN **Test timeout** '$_pb_v' is not a whole number of seconds ($PCT_TIMEOUT_MIN-$PCT_TIMEOUT_MAX) -- using $PCT_TIMEOUT_DEFAULT" >&2 ;;
      *)
        if [ "${#_pb_v}" -le 5 ] && [ "$_pb_v" -ge "$PCT_TIMEOUT_MIN" ] && [ "$_pb_v" -le "$PCT_TIMEOUT_MAX" ]; then
          _pb_b=$_pb_v
        else
          echo "pre-commit-test: WARN **Test timeout** '$_pb_v' is outside $PCT_TIMEOUT_MIN-$PCT_TIMEOUT_MAX -- using $PCT_TIMEOUT_DEFAULT" >&2
        fi ;;
    esac
  fi
  case "${PCT_TEST_TIMEOUT_TESTONLY_S:-}" in
    [1-9]|1[0-9]|2[0-9]) _pb_b=$PCT_TEST_TIMEOUT_TESTONLY_S ;;
  esac
  printf '%s\n' "$_pb_b"
}

# pct_group_members <pgid> -- how many live processes are still in the group.
# Git Bash/Cygwin: /proc/<pid>/pgid (read with the builtin -- no spawn per
# process). Elsewhere: ps -A -o pgid=.
pct_group_members() {
  _pg_n=0
  if [ -r "/proc/$$/pgid" ]; then
    for _pg_d in /proc/[0-9]*; do
      _pg_g=""
      { read -r _pg_g < "$_pg_d/pgid"; } 2>/dev/null
      [ "$_pg_g" = "$1" ] && _pg_n=$((_pg_n + 1))
    done
  else
    _pg_n=$(ps -A -o pgid= 2>/dev/null | awk -v g="$1" '$1 == g { n++ } END { print n + 0 }')
  fi
  printf '%s\n' "${_pg_n:-0}"
}

# pct_kill_group <pgid> -- every member's native subtree first (Git Bash), then
# the whole group.
pct_kill_group() {
  if [ -r "/proc/$$/pgid" ]; then
    for _pk_d in /proc/[0-9]*; do
      _pk_g=""
      _pk_w=""
      { read -r _pk_g < "$_pk_d/pgid"; } 2>/dev/null
      [ "$_pk_g" = "$1" ] || continue
      { read -r _pk_w < "$_pk_d/winpid"; } 2>/dev/null
      [ -n "$_pk_w" ] && taskkill //F //T //PID "$_pk_w" >/dev/null 2>&1
    done
  fi
  kill -KILL -- "-$1" 2>/dev/null
  return 0
}

# pct_run_bounded <budget_s> <outfile> <command...> -- runs the command in a
# subshell (the containment of a consumer value's exit/exec, v2.2.5 round 5,
# lives HERE now), in its own process group, stdin from /dev/null, output to
# <outfile>. Sets PCT_RC to its exit status, or to the string "timeout" when
# the budget ran out and the group was killed; PCT_LEFT counts survivors.
# $SECONDS, not date: no spawn per tick, and a loaded machine slows the loop,
# not the clock.
pct_run_bounded() {
  _rb_budget=$1
  _rb_out=$2
  shift 2
  PCT_LEFT=0
  set -m
  ( "$@" ) > "$_rb_out" 2>&1 < /dev/null &
  _rb_pid=$!
  set +m
  # Its own process group no longer dies with this hook: if the hook itself is
  # signalled (a cancel, or a harness timeout on a registration without the
  # field), take the group down first. An uncatchable kill (SIGKILL,
  # TerminateProcess) still orphans it -- a stated Known limit.
  trap 'pct_kill_group "$_rb_pid"; exit 2' TERM INT HUP
  _rb_t0=$SECONDS
  _rb_ceil=$(pct_ceiling)
  PCT_CEIL_HIT=0
  while kill -0 "$_rb_pid" 2>/dev/null; do
    # Either limit stops the run: the per-run budget (counted from the fork) or
    # the hook-wide ceiling (bash $SECONDS counts from hook start).
    if [ "$SECONDS" -ge "$_rb_ceil" ] && [ $((SECONDS - _rb_t0)) -lt "$_rb_budget" ]; then
      PCT_CEIL_HIT=1
    fi
    if [ $((SECONDS - _rb_t0)) -ge "$_rb_budget" ] || [ "$PCT_CEIL_HIT" -eq 1 ] || [ "$SECONDS" -ge "$_rb_ceil" ]; then
      pct_kill_group "$_rb_pid"
      wait "$_rb_pid" 2>/dev/null
      for _rb_i in 1 2 3 4 5; do
        PCT_LEFT=$(pct_group_members "$_rb_pid")
        [ "$PCT_LEFT" -eq 0 ] 2>/dev/null && break
        pct_kill_group "$_rb_pid"
        sleep 1
      done
      trap - TERM INT HUP
      PCT_RC=timeout
      return 0
    fi
    sleep 1
  done
  wait "$_rb_pid"
  PCT_RC=$?
  trap - TERM INT HUP
  return 0
}

# pct_refuse_timeout <budget> <label> -- the over-budget refusal. Never returns.
pct_refuse_timeout() {
  if [ "${PCT_CEIL_HIT:-0}" -eq 1 ]; then
    echo "BLOCKED: pre-commit-test: the hook-wide ceiling ($(pct_ceiling) s since hook start, set by the harness timeout $((PCT_TIMEOUT_MAX + 60)) s) was reached before the $1 s budget ran out -- commit refused. Shorten the per-commit Test or lower **Test timeout**." >&2
  else
  echo "BLOCKED: pre-commit-test: Test exceeded its $1 s budget -- commit refused. Shorten the per-commit Test (fast subset) and move the full suite to **Gate** / **Gate extra**, or raise **Test timeout** (max $PCT_TIMEOUT_MAX)." >&2
  fi
  echo "  stopped: '$2' and every process it started" >&2
  if [ "${PCT_LEFT:-0}" -gt 0 ] 2>/dev/null; then
    echo "  WARN: $PCT_LEFT process(es) of that run were still alive after the kill -- check for orphans before re-running" >&2
  fi
  echo "--- last 20 lines ---" >&2
  tail -20 "$OUT" >&2
  rm -f "$OUT"
  exit 2
}

gc_read_stdin
gc_guard_off && exit 0

# Fail CLOSED on a pre-v2 settings.json: it registers this gate on the retired
# git-tools MCP tools, whose payloads carry no tool_input.command — the v2 gate
# would find nothing to parse and allow the commit.
case "$GC_TOOL" in
  mcp__git-tools__git_push|mcp__git-tools__git_commit)
    echo "BLOCKED: settings.json predates this hook (MCP matcher) — restart the session after /sync-template" >&2
    exit 2 ;;
esac

# v2.2.6 round 2 — THE 14th FAIL-OPEN. A bare `[ -n "$GC_CMD" ] || exit 0` stood
# here, and a traced `git commit` reached it with an empty GC_CMD on a payload
# that parsed: the gate exited 0 in silence and the commit completed in ~1 s
# against an 87 s **Test**. See gc_cmd_unreadable in hooks/lib/git-cmd.sh for the
# state split and for why the polarity is CONDITIONAL rather than inverted
# outright — an unconditional refusal here would hard-block every Bash call.
if gc_cmd_unreadable; then
  pct_note unreadable -1
  echo "BLOCKED: pre-commit-test: the payload carries a command this gate could not read, so it cannot show your tests passed — refusing rather than allowing an unverified commit. Re-run the commit. (If it repeats: create '.claude/git-guard-off' under this cwd, make the one fix, then delete it.)" >&2
  exit 2
fi

[ -n "$GC_CMD" ] || { pct_note empty-cmd -1; exit 0; }

# v4.3.1 S6 -- EXACT FAST PATH for a command that cannot reach a commit. This
# hook runs on EVERY Bash call; the walk below cost ~1.4 s on an idle machine
# for `ls -la`. Everything past this point can only refuse a command whose text
# (quotes and backslashes removed, case ignored; v4.3.2 6b: the verb matchers
# remove a backslash too, so `git com\mit` is gated and walks) holds one of
# these words:
#   commit            the gated verb itself (`git com"mit"`, `GIT COMMIT`);
#   merge pull push   gc_dir_rule (simple-cd rule) refuses these, and `gh pr merge`,
#                     after a directory change even with no `commit` in the text;
#   sh                the WORD sh or sh.exe (/bin/sh, C:\Git\bin\sh.exe; never x.sh or --short), and the substrings
#                     bash, pwsh, powershell -- the words that make the walk read
#                     a script body (gc_script_body, gc_seg_is_ps);
#   source, `.`       `source` (substring), and a `.` token: lone or ending in `/.`
#                     (`. x`, `ls;. x`, `x/. c.sh`; never `./x` or a prose dot);
#   [ ? *             a glob character: the walk expands globs, so the word match
#                     cannot judge the text for certain.
# With none of them nothing below can refuse, so the walk is skipped and the same
# no-op record is written. Zero forks: pure parameter expansion and case. Any
# doubt keeps the walk -- this only ever skips work.
pct_t=${GC_CMD//\"/}; pct_t=${pct_t//\'/}; pct_t=${pct_t//\\/}
# v4.3.2 F1 -- `sh` and `.` are matched as WORDS: as substrings they fired on
# --short, publish, stylish, every x.sh name, and every sentence of prose. A
# word: the text with each character outside [[:alnum:]._] made a space, so
# `/bin/sh` fires and `x.sh`/`--short` do not; a lone dot: the text with
# whitespace and ; & | ( ) { } ! ` < > made spaces, so `. x`, `ls;. x` and a token
# ending in `/.` (`x/. c.sh`: the walk takes its basename as a dot-source) fire and
# `./x`, `..` and `end.` do not. bash, pwsh and powershell are named
# explicitly (*sh* used to cover them); the verb stems stay substrings. A glob
# character ([ ? *) always walks: the walk expands globs (`set -- $seg` in
# gc_script_body), so `/bin/[s]h c.sh` or `/bin/?h c.sh` runs a script the word
# match cannot see.
# Superset proof: design F1 (words and lone dots), plus the glob rule. The walk's
# only expansions of the text are IFS splitting (narrower than the word
# separators) and pathname expansion (needs [ ? *), in gc_script_body and
# gc_seg_is_ps; so a text with none of those characters is judged exactly by the
# word match. The bracket patterns live in variables: a literal `}` inside
# ${...} would end the expansion.
pct_nw='[^[:alnum:]._]'; pct_sep='[[:space:];&|(){}!`<>]'
pct_u=${GC_CMD//\"/}; pct_u=${pct_u//\'/}   # quotes out, backslashes kept: C:\Git\bin\sh.exe
pct_w=" ${pct_t//$pct_nw/ } "; pct_w2=" ${pct_u//$pct_nw/ } "; pct_d=" ${pct_t//$pct_sep/ } "
pct_walk=0
shopt -s nocasematch
case "$pct_t" in *commit*|*merge*|*pull*|*push*|*source*|*bash*|*pwsh*|*powershell*|*[[?*]*) pct_walk=1 ;; esac
case "$pct_w$pct_w2" in *" sh "*|*" sh.exe "*) pct_walk=1 ;; esac
case "$pct_d" in *" . "*|*"/. "*) pct_walk=1 ;; esac
shopt -u nocasematch
[ "$pct_walk" = 1 ] || { pct_note no-commit-segment -1; exit 0; }

# v4.0.3 item 12 -- widen GC_CMD to include the body of any script segment it
# invokes (`bash|sh|source|. <path>`, depth 1) BEFORE splitting into segments,
# so a `git commit` inside such a script is gated exactly as if typed. See
# gc_script_body / gc_augmented_cmd in hooks/lib/git-cmd.sh for the 16 KB cap
# and the depth-1/TOCTOU residuals. cmd_len in the diagnostic artifact below
# reflects the augmented length, in BYTES, on this path -- accepted, it is a
# diagnostic field, not a gate.
# The command as typed, before the script-body widening: the **Test paths** skip
# (v4.3.0 A1, S-28) judges THIS text, never the widened one.
PCT_RAW_CMD="$GC_CMD"
# v4.3.1 S-3c: gc_dir_rule is the simple-cd rule (lib): it widens GC_CMD as above
# and refuses a gated command that changes directory in any way but one leading
# `cd <absolute dir> &&`. GC_CWD_E is the directory everything is judged in.
gc_dir_rule pre-commit-test "$GC_CWD" || { pct_note dir-change -1; exit 2; }

# Judge every commit segment of the command line (v4.3.1 G6 / T3-3), not only
# the first.
base="$GC_CWD_E"
REPO_PATH=""
segments=$(gc_segments)
# gc_seg_quoted (lib) is a sibling of gc_segments: one 0|1 line per line of
# $segments, same order -- see its header note on why this cannot be a
# variable gc_segments sets as a side effect (a command-substitution subshell
# would discard it).
GC_SEG_QUOTED=$(gc_seg_quoted)
pct_seg_quoted="$GC_SEG_QUOTED"

# Every commit segment runs in GC_CWD_E (S-3c): the one leading cd's target, else
# the payload cwd. Its repository is the top-level of that directory.
pct_tops=""
pct_ntops=0
pct_seen_commit=0
pct_seg_idx=0
while IFS= read -r seg; do
  pct_seg_idx=$((pct_seg_idx + 1))
  [ -n "$seg" ] || continue

  if gc_matches_subcommand "$seg" "commit"; then
    # v3.1 -- resolve matched_in_quoted as soon as the commit segment is
    # known, before any of the pct_note calls below (global-refused,
    # unresolved-c, gate, test) that must all carry it. (The FIRST commit
    # segment's flag stands for the artifact.)
    if [ "$pct_seen_commit" = 0 ]; then
      case "$(printf '%s\n' "$pct_seg_quoted" | sed -n "${pct_seg_idx}p")" in
        1) PCT_QUOTED=true ;;
        *) PCT_QUOTED=false ;;
      esac
    fi
    pct_seen_commit=1
    # --- v3.0.3 (finding 62), one block, deliberately small ------------------
    # A global before `commit` used to make the line above false, so this gate
    # exited 0 in 0 s having run no tests: `git -P commit -m x` and
    # `git --no-pager commit` were measured skipping the suite entirely while
    # the control `git commit -m x` ran it in 5 s. Both exited 0, so the EXIT
    # CODE cannot discriminate on a green suite — the signal is whether the
    # suite ran. v4.0.1: the lib's positional walk in gc_matches_subcommand is
    # now the SOLE authority (the GC_GIT_PRE fast path that originally fixed
    # this is retired -- see hooks/lib/git-cmd.sh) and it finds the subcommand
    # regardless of the globals; the globals are classified here by the same
    # gc_global_options the other two git gates use. An inert global
    # (`-C <path>`, `--no-pager`, `-P`, …) falls through and the Test runs
    # normally.
    pctg=$(gc_global_options "$seg")
    if [ "$pctg" != ok ]; then
      case "$pctg" in
        refuse:*) pctgopt="${pctg#refuse:}" ;;
        env:*)    pctgopt="${pctg#env:}=" ;;
      esac
      {
        echo "BLOCKED: pre-commit-test refuses this commit: it carries the global option '$pctgopt' before the subcommand."
        echo "  matched segment: $seg"
        echo "  verdict: refused. A global option that changes what the command RESOLVES to (config, repo, or binaries), or one unknown to this gate, means the repository this hook would test is not provably the repository this commit lands in."
        echo "  allowed globals: -C <path>, --no-pager, -P, --paginate, --no-optional-locks, --literal-pathspecs, --no-lazy-fetch."
        echo "Re-run the commit WITHOUT the '$pctgopt' option; set it in your configuration in a separate call instead."
      } >&2
      # v3.0.3 Task 8½ — NAME THE REFUSAL IN THE ARTIFACT. On a green suite the
      # skipped and the run case both exit 0, and the `passed. (` marker is
      # absent in the broken state AND in the fixed one, so neither channel can
      # carry finding 62's commit half. The artifact's `path` field can:
      # `no-commit-segment` before the lib fix, `global-refused` after.
      pct_note global-refused -1
      exit 2
    fi
    # --- end v3.0.3 block ----------------------------------------------------

    # v3.0.3 defect 3a — WIRE THE `-C` RESOLVER BEFORE gc_repo_for, same
    # position no-push-main.sh and gate-before-merge.sh already use. Until
    # now this hook called gc_repo_for directly with no preceding
    # unresolved-`-C` check at all, so a `-C` fold this hook cannot resolve
    # (cannot-determine, or -- pre-defect-2 -- a multi-`-C` fold where nothing
    # resolves) silently fell back to `$base` and the wrong repository's Test
    # command ran instead of a refusal.
    pctdu_out=$(gc_dash_c_unresolved "$seg" "$base")
    if [ -n "$pctdu_out" ]; then
      pctdu_kind=$(printf '%s\n' "$pctdu_out" | sed -n 1p)
      pctdu=$(printf '%s\n' "$pctdu_out" | sed -n 2p)
      if [ "$pctdu_kind" = cannot-determine ]; then
        echo "BLOCKED: pre-commit-test: hook cannot DETERMINE the -C target (contains an unexpanded shell expression): $pctdu" >&2
        echo "  matched segment: $seg" >&2
      else
        echo "BLOCKED: pre-commit-test: hook could not resolve \`-C $pctdu\`; if git can, pass an absolute path." >&2
        echo "  matched segment: $seg" >&2
      fi
      pct_note unresolved-c -1
      exit 2
    fi

    # v4.3.1 G6 (ruling P-2) -- READ THE CONFIG AT THE REPOSITORY TOP-LEVEL.
    # gc_repo_for is the directory the commit segment runs in (the leading cd, a
    # payload cwd of sub/, a `git -C sub`), and the reads below used to take
    # $REPO_PATH/PROJECT_CONTEXT.md literally: from a subdirectory without one
    # the "nothing to run" arm allowed the commit with no Test (measured 0 at
    # 3a901fe). A nested repository is its own top-level. No top-level ->
    # refuse: this gate cannot show the tests passed, and git would fail such a
    # commit anyway.
    pct_rp=$(gc_repo_for "$seg" "$base")
    PCT_TOP=$(git -C "$pct_rp" rev-parse --show-toplevel 2>/dev/null)
    if [ -z "$PCT_TOP" ] || [ ! -d "$PCT_TOP" ]; then
      pct_note no-toplevel -1
      echo "BLOCKED: pre-commit-test: cannot find the repository top-level for '$pct_rp' -- refusing rather than committing with no Test. Run the commit from inside the repository (or pass git -C <repo>)." >&2
      exit 2
    fi
    case "$GC_NL$pct_tops$GC_NL" in
      *"$GC_NL$PCT_TOP$GC_NL"*) ;;
      *) pct_tops="$pct_tops$GC_NL$PCT_TOP"; pct_ntops=$((pct_ntops + 1)); REPO_PATH="$PCT_TOP" ;;
    esac
  fi
done <<GC_SEGMENTS
$segments
GC_SEGMENTS

# Not a commit -- nothing to gate.
[ "$pct_seen_commit" = 1 ] || { pct_note no-commit-segment -1; exit 0; }
# v4.3.1 T3-3: commits in more than one repository in one command. One Test
# cannot answer for both, and judging only the first let a failing repository's
# commit through behind a green one. Fail closed.
if [ "$pct_ntops" -gt 1 ]; then
  pct_note multi-repo -1
  {
    echo "BLOCKED: pre-commit-test: this command commits in more than one repository (or in one of several repositories the hook cannot tell apart):"
    printf '%s\n' "$pct_tops" | sed '/^$/d;s/^/  /'
    echo "  verdict: refused. One Test cannot answer for several repositories -- commit each repository in a separate call."
  } >&2
  exit 2
fi
[ -n "$REPO_PATH" ] || { pct_note no-toplevel -1; echo "BLOCKED: pre-commit-test: cannot find the repository top-level -- refusing rather than committing with no Test." >&2; exit 2; }

# From here the artifact goes to the repo the COMMIT targets, which `git -C` and
# a `cd` clause can point anywhere. Absolute, and fixed before any cd below.
PCT_ARTIFACT_BASE="$REPO_PATH"

# v4.3.0 A1 -- **Test paths** (opt-in). Unset, empty or an unfilled placeholder
# = test everything (today). When set: skip the Test line only if NO changed
# path -- staged, unstaged or untracked (R-C: `git add x && git commit` has not
# staged x yet when this hook runs) -- matches the pathspecs. set -f keeps the
# shell from expanding a glob pathspec against the cwd. git failing to
# evaluate the pathspecs falls through to the test run (fail-closed).
#
# v4.3.0 A1 fix (final review I-1, ruling S-28): the tree this hook inspects is
# the tree BEFORE the command runs, so any clause ahead of the commit (git rm,
# git mv, sed -i, a redirect, a script) can change a matching path without the
# `git status` below seeing it, and `git commit -a` / `-i` / `-o` / a pathspec
# commits paths that `git status` does not report the way this decision needs.
# The skip therefore applies ONLY to a lone `git commit` (pct_single_commit);
# anything else, and any doubt, runs the tests.
pct_single_commit() { # <raw command> -- 0 only for one plain `git commit`, nothing else
  local s="$1" n i=0 ch q="" tok="" have=0 k=0 nt t cl c
  local -a toks=()
  s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"
  # Ruling S-33: Claude Code's standard commit form, `-m "$(cat <<'X'` <body> `X` `)"`
  # at the very end of the command, is a lone commit. Only with a QUOTED delimiter
  # (the body is then literal text) and only when the text after the terminator
  # line is exactly `)"`. The whole substitution is replaced by a plain word and the
  # rest goes through the scanner below, so the flags before `-m` still obey the
  # -a/-i/-o/pathspec rule. Anything else (unquoted or `<<-`, another substitution,
  # trailing text, a second command) is not matched here and the scanner refuses `$`.
  local hd='"$(cat <<' hhead hrest hq hx hline
  case "$s" in
    *"$hd"*)
      hhead="${s%%"$hd"*}"; hrest="${s#*"$hd"}"
      case "$hhead" in *[[:space:]]-m[[:space:]]) ;; *) return 1 ;; esac
      hq="${hrest:0:1}"
      case "$hq" in "'"|'"') ;; *) return 1 ;; esac
      hrest="${hrest:1}"; hx="${hrest%%"$hq"*}"
      [[ "$hx" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
      hrest="${hrest#"$hx$hq"}"
      [ "${hrest:0:1}" = $'\n' ] || return 1
      hrest="${hrest:1}"
      while :; do
        case "$hrest" in *$'\n'*) ;; *) return 1 ;; esac
        hline="${hrest%%$'\n'*}"; hrest="${hrest#*$'\n'}"
        # Ruling S-34: bash ends the body at a line that STARTS with the delimiter
        # followed by `)` and runs the rest of that line. Any line that starts with the
        # delimiter but is not exactly it is doubt: not a lone commit.
        case "$hline" in "$hx") break ;; "$hx"*) return 1 ;; esac
      done
      [ "$hrest" = ')"' ] || return 1
      s="${hhead}x" ;;
  esac
  n=${#s}
  while [ "$i" -lt "$n" ]; do
    ch="${s:$i:1}"; i=$((i + 1))
    case "$q" in
      "'") if [ "$ch" = "'" ]; then q=""; else tok="$tok$ch"; fi; continue ;;
      '"')
        case "$ch" in
          '"') q="" ;;
          '$'|'`') return 1 ;;
          '\') tok="$tok${s:$i:1}"; i=$((i + 1)) ;;
          *) tok="$tok$ch" ;;
        esac
        continue ;;
    esac
    case "$ch" in
      "'") q="'"; have=1 ;;
      '"') q='"'; have=1 ;;
      '\'|'&'|';'|'|'|'<'|'>'|'('|')'|'{'|'}'|'$'|'`'|'!'|'#'|'*'|'?'|'['|$'\n'|$'\r') return 1 ;;
      ' '|$'\t') if [ "$have" = 1 ] || [ -n "$tok" ]; then toks+=("$tok"); tok=""; have=0; fi ;;
      *) tok="$tok$ch" ;;
    esac
  done
  [ -z "$q" ] || return 1
  if [ "$have" = 1 ] || [ -n "$tok" ]; then toks+=("$tok"); fi
  nt=${#toks[@]}
  [ "$nt" -ge 2 ] || return 1
  [ "${toks[0]}" = git ] || return 1
  k=1
  while [ "$k" -lt "$nt" ]; do
    case "${toks[$k]}" in
      -C) k=$((k + 2)) ;;
      --no-pager|-P|--paginate|--no-optional-locks|--literal-pathspecs) k=$((k + 1)) ;;
      commit) break ;;
      *) return 1 ;;
    esac
  done
  [ "$k" -lt "$nt" ] && [ "${toks[$k]}" = commit ] || return 1
  k=$((k + 1))
  while [ "$k" -lt "$nt" ]; do
    t="${toks[$k]}"; k=$((k + 1))
    case "$t" in
      --) return 1 ;;
      --message|--file|--author|--date|--cleanup|--template|--reuse-message|--reedit-message) k=$((k + 1)) ;;
      --message=*|--file=*|--author=*|--date=*|--cleanup=*|--template=*|--gpg-sign=*) ;;
      --amend|--no-verify|--allow-empty|--allow-empty-message|--no-edit|--edit|--signoff|--no-signoff|--verbose|--quiet|--no-gpg-sign|--gpg-sign|--no-post-rewrite|--reset-author) ;;
      --*) return 1 ;;
      -?*)
        cl="${t#-}"
        while [ -n "$cl" ]; do
          c="${cl:0:1}"; cl="${cl:1}"
          case "$c" in
            m|F|C|c|t) [ -z "$cl" ] && k=$((k + 1)); cl="" ;;
            S) cl="" ;;
            n|s|v|q|e) ;;
            *) return 1 ;;
          esac
        done ;;
      *) return 1 ;;
    esac
  done
  return 0
}
TEST_PATHS=$(grep -E "${GC_KEY_PRE}\*\*Test paths\*\*:" "$REPO_PATH/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Test paths\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)
case "$TEST_PATHS" in *\{\{*\}\}*) TEST_PATHS="" ;; esac
if [ -n "$TEST_PATHS" ] && ! pct_single_commit "$PCT_RAW_CMD"; then
  echo "pre-commit-test: **Test paths** applies only to a lone \`git commit\` (no chained command, -a/-i/-o or pathspec) -- running the tests" >&2
  TEST_PATHS=""
fi
if [ -n "$TEST_PATHS" ]; then
  set -f
  # v4.3.0 A1 fix round 1 (S-4): git pathspec magic (a word beginning with
  # `:`, e.g. `:(exclude)*`, `:!x`) can make `git status ... -- $TEST_PATHS`
  # exit 0 with EMPTY output regardless of the real changes -- a silent,
  # permanent skip that the existing fail-closed guard (which only catches a
  # non-zero git exit) does not catch. Detected before the git call, with
  # set -f still in effect since a plain word may itself be a glob.
  _tp_magic=""
  # shellcheck disable=SC2086 # word-splitting the pathspec list is intended
  for _tp_w in $TEST_PATHS; do
    case "$_tp_w" in
      :*) _tp_magic=1 ;;
    esac
  done
  if [ -n "$_tp_magic" ]; then
    set +f
    echo "pre-commit-test: WARN **Test paths** uses git pathspec magic (':...'), which can match nothing -- ignoring it and running the tests" >&2
  else
    # v4.3.0 A1 fix round 2 (S-5): a word that matches NO tracked file --
    # a typo (srcc/), a renamed/removed directory, or literal quotes that
    # reached this hook as part of the word itself ("src/") -- makes
    # `git status ... -- $TEST_PATHS` exit 0 with EMPTY output the same way
    # pathspec magic does: a silent, permanent skip. Validated one word at a
    # time (quoted, so a real glob word is not re-expanded by the shell here;
    # set -f is still in effect from above) against `git ls-files`, which
    # must print at least one line for a word to count as real. Only when
    # EVERY word validates does the existing git-status skip decision apply.
    _tp_invalid=""
    for _tp_w in $TEST_PATHS; do
      if ! _tp_lsout=$(git -C "$REPO_PATH" ls-files -- "$_tp_w" 2>/dev/null) || [ -z "$_tp_lsout" ]; then
        _tp_invalid="$_tp_w"
        break
      fi
    done
    if [ -n "$_tp_invalid" ]; then
      set +f
      echo "pre-commit-test: WARN **Test paths** entry '$_tp_invalid' matches no tracked file -- ignoring **Test paths**, running the tests" >&2
    else
      # shellcheck disable=SC2086 # word-splitting the pathspec list is intended
      if _tp_hits=$(git -C "$REPO_PATH" status --porcelain --untracked-files=all -- $TEST_PATHS 2>/dev/null); then
        set +f
        if [ -z "$_tp_hits" ]; then
          echo "pre-commit-test: no changed path matches **Test paths** ($TEST_PATHS) -- tests skipped for this commit; the merge gate still runs in full" >&2
          pct_note test-paths-skip 0
          exit 0
        fi
      fi
      set +f
    fi
  fi
fi

# Read test command from PROJECT_CONTEXT.md through GC_KEY_PRE (see the header
# note on that constant in hooks/lib/git-cmd.sh: a leading UTF-8 BOM otherwise
# hides a key that sits on line 1, and THIS hook's no-field arm is warn+allow).
# Tolerates: leading "- " / "* " list
# markers, the "**Test Command**:" label style (java/python variants), and
# surrounding backticks — several variants write commands as `cmd`.
# v2.1.3 fix round 1: **Test** always wins when present -- cheap, unchanged
# behaviour for repos that declare a lightweight Test command. run-gate.sh is
# only consulted below when there is NO Test field.
# v3.0.3 defect 3b — anchored at GC_KEY_PRE (same grammar the grep above
# uses), not a greedy `.*`: a value that itself contains the literal text
# `**Test**:` a second time used to have everything up to THAT occurrence
# stripped too, truncating the extracted command instead of returning the
# whole original value.
TEST_CMD=$(grep -E "${GC_KEY_PRE}\*\*Test( Command)?\*\*:" "$REPO_PATH/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Test( Command)?\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)

# v2.1.3 fix round 2: a still-unfilled {{...}} Test placeholder must not win
# precedence over a real Gate command -- dotnet/dotnet-maui ship exactly this
# shape (a Test field still holding the TEST_COMMAND placeholder in its
# double-brace form, beside a real Gate). v3.0.3: the placeholder is NAMED here
# rather than written literally — a literal one is bait for the consumer-side
# placeholder sweep in the sync-template skill, which reports it as an unfilled
# token in a shipped file. Benign only for as long as that sweep keeps its
# comment filter. Strip it to "" here, right after
# extraction, so it is treated as absent below and precedence correctly falls
# through to the Gate/run-gate.sh path instead of silently exiting 0.
case "$TEST_CMD" in
  *\{\{*\}\}*) TEST_CMD="" ;;
esac

# v3.0.3 Task 8½ — `none` IS AN OPT-OUT, NOT A COMMAND. Measured 2026-09-04 on
# the shipped hook: `- **Test**: none` printed `PRE-COMMIT: Running 'none'...`,
# took 127 from the shell and BLOCKED every commit in that repository. The field
# whose value reads as "I have no Test command" was the one value that hard-
# blocked, and the release plan itself advised writing it. `none` is already how
# the Protected-branches field spells "opt out", so the Test field spells it the
# same way — and is then treated exactly like an absent field, so precedence
# still falls through to the Gate/run-gate.sh path below rather than exiting 0.
# Case-insensitive; the extraction above has already trimmed surrounding space
# and stripped the backticks several variants write commands in.
case "$(printf '%s' "$TEST_CMD" | tr '[:upper:]' '[:lower:]')" in
  none) TEST_CMD="" ;;
esac

# v2.1.1: projects that declare only a **Gate** command (the gate runs the tests
# plus format/lint) used to make this hook a silent no-op. Fall back to Gate.
#
# v2.1.3 (consumer feedback, Yutraffic; fix round 1): when the fallback fires
# AND hooks/run-gate.sh sits next to this script, run run-gate.sh instead of
# eval'ing the Gate command ourselves. A green run writes
# .gate/last-pass.json as a side effect, so gate-before-merge.sh is satisfied
# without a second gate run at merge time. A still-unfilled {{...}} placeholder
# is treated as absent here (never routed into run-gate.sh, and never eval'd
# directly) -- it falls through to the "nothing to run" WARN below, same as no
# Gate field at all, so a mid-setup repo cannot get a false green.
if [ -z "$TEST_CMD" ]; then
  # v3.0.3 defect 3b — same GC_KEY_PRE-anchored fix as the **Test** and
  # **Protected branches** extractors: no greedy `.*`.
  GATE_CMD_RAW=$(grep -E "${GC_KEY_PRE}\*\*Gate( Command)?\*\*:" "$REPO_PATH/PROJECT_CONTEXT.md" 2>/dev/null | sed -E "s/${GC_KEY_PRE}\\*\\*Gate( Command)?\\*\\*:[[:space:]]*//;s/[[:space:]]*\$//;s/^\`//;s/\`\$//" | head -1)
  case "$GATE_CMD_RAW" in
    *\{\{*\}\}*) GATE_CMD_RAW="" ;;
  esac

  if [ -n "$GATE_CMD_RAW" ]; then
    if [ -f "$RUN_GATE" ]; then
      echo "PRE-COMMIT: Running 'run-gate.sh'..." >&2
      # v2.2.5 round 4: exit 2, NOT 1. The harness treats every non-zero, non-2
      # PreToolUse exit as a NON-BLOCKING error and lets the tool call proceed
      # (see the exit-code conventions in hooks/lib/git-cmd.sh) -- so the former
      # `exit 1` here was warn-and-ALLOW: a failed cd into the resolved repo let
      # the commit through UNGATED. Cannot-determine must refuse.
      # Deliberately 2 and not GC_TERMINAL_RC: per the same reasoning as
      # run-gate.sh's own `cd "$REPO_TOP" || exit 1`, a cd failing on a path git
      # just resolved is a transient FAULT (race, permissions, unmounted share),
      # not a settled condition, so "re-run it" is honest advice. And 78 is an
      # internal signal that is never a hook's own exit status.
      cd "$REPO_PATH" || { pct_note gate -1; echo "BLOCKED: pre-commit-test: cannot enter the repository at '$REPO_PATH' — re-run the commit once the path is reachable." >&2; exit 2; }
      OUT=$(mktemp 2>/dev/null || echo "$REPO_PATH/.pre-commit-test.out")
      pct_capture_tree
      PCT_BUDGET=$(pct_budget "$REPO_PATH")
      pct_run_bounded "$PCT_BUDGET" "$OUT" bash "$RUN_GATE"
      if [ "$PCT_RC" = timeout ]; then
        pct_note gate '"timeout"'
        pct_refuse_timeout "$PCT_BUDGET" "run-gate.sh"
      fi
      pct_note gate "$PCT_RC"
      if [ "$PCT_RC" -eq 0 ]; then
        rm -f "$OUT"
        echo "PRE-COMMIT: 'run-gate.sh' passed." >&2
        exit 0
      else
        # v2.2.5: suppress the retry advice STRUCTURALLY on a terminal rc. This
        # test names no guard and reads no message, so any future terminal guard
        # inherits it by exiting GC_TERMINAL_RC. The captured tail is printed
        # last either way, so the guard's own specific remedy is what the user
        # reads at the bottom of the block.
        if [ "$PCT_RC" -eq "$GC_TERMINAL_RC" ]; then
          echo "BLOCKED: 'run-gate.sh' cannot succeed as configured — this is a configuration failure, not a failing check. Re-running it will NOT help; apply the remedy below." >&2
        else
          echo "BLOCKED: 'run-gate.sh' failed — re-run it and fix the failures before committing." >&2
          # v2.2.5: name the escape hatch WHERE THE FAILURE SURFACES. Since the
          # toolkit gates itself, a bug in this hook can block the very commit
          # that fixes it — and someone hard-blocked mid-commit is not reading
          # CLAUDE.md. Same principle as the terminal-remedy rule above: the
          # remedy has to appear where the person actually is.
          echo "  (If the HOOK itself is broken rather than the suite: create '.claude/git-guard-off' under this cwd, make the one fix, then delete it. Never leave it in place.)" >&2
        fi
        # v3.0.3 (queue item 4, consumer-authored, verbatim). BEFORE the tail
        # header on purpose: everything after that header must be the gate's own
        # output, so its remedy stays the last thing on stderr.
        echo "Could not determine: this check ran your suite against the WORKING TREE as it stood a moment ago, not against the tree this commit will contain. If only part of the tree is staged, or it changed between that run and this commit, the thing tested and the thing committed are different objects." >&2
        echo "--- last 20 lines ---" >&2
        tail -20 "$OUT" >&2
        rm -f "$OUT"
        exit 2
      fi
    else
      # v2.1.3 fix round 2: a mirror (e.g. ~/.claude/hooks/) whose run-gate.sh
      # copy was never migrated must not 127 -- fall back to eval'ing the Gate
      # command directly, same as the pre-run-gate.sh behaviour, but say so:
      # a silent fallback here reads exactly like the full-gate path ran.
      echo "WARN: pre-commit-test: run-gate.sh not found next to this hook — evaluating the Gate command directly instead" >&2
      TEST_CMD="$GATE_CMD_RAW"
    fi
  fi
fi

# Nothing to run. Say so — a silent pass reads exactly like a green test run.
if [ -z "$TEST_CMD" ]; then
  pct_note nothing-to-run -1
  echo "WARN: pre-commit-test: no Test/Gate command in PROJECT_CONTEXT.md — nothing verified" >&2
  exit 0
fi

# v2.1.4: the placeholder-{{...}} guard formerly here is unreachable -- both
# TEST_CMD's own extraction (line ~87) and GATE_CMD_RAW's (line ~104) already
# strip a {{...}} placeholder to "", and an empty TEST_CMD exits at line 137
# above before this point is ever reached.

echo "PRE-COMMIT: Running '$TEST_CMD'..." >&2
# Same as the run-gate.sh branch above: `exit 1` from a PreToolUse hook is
# warn-and-ALLOW, so this must be 2 or a failed cd waves the commit through.
cd "$REPO_PATH" || { pct_note test -1; echo "BLOCKED: pre-commit-test: cannot enter the repository at '$REPO_PATH' — re-run the commit once the path is reachable." >&2; exit 2; }

# Capture rather than discard: with the Gate fallback $TEST_CMD may be a whole
# gate, and "it failed" with no output leaves nothing to act on. Bounded to the
# last 20 lines so a chatty gate cannot flood the transcript.
OUT=$(mktemp 2>/dev/null || echo "$REPO_PATH/.pre-commit-test.out")
PCT_T0=$(date +%s 2>/dev/null || echo 0)
# THE SUBSHELL IS LOAD-BEARING (v2.2.5 round 5, independent QA at 9baa446;
# pre-existing since v2.1.x, not a regression of this branch).
#
# `eval` runs its argument in the CURRENT shell. `$TEST_CMD` is a CONSUMER-
# authored value, so a value whose top level reaches `exit` or `exec` terminated
# THIS HOOK and bypassed the if/else below entirely. Measured, bare `eval`:
#
#   **Test**: exit 1                 -> hook exit 1,  ZERO "BLOCKED" lines
#   **Test**: exec bash -c "exit 1"  -> hook exit 1,  ZERO "BLOCKED" lines
#   **Test**: exec bash -c "exit 78" -> hook exit 78, ZERO "BLOCKED" lines
#
# A non-2 PreToolUse exit is warn-and-ALLOW, so each of those let the commit
# through UNGATED and SILENTLY, and leaked $OUT. The 78 case additionally
# violated the invariant this release documents in hooks/lib/git-cmd.sh: 78 is
# never a hook's own exit status, and a hook NEVER forwards a child's code.
#
# `( ... )` contains both: `exit` ends the subshell and `exec` replaces the
# subshell's process, and either way $? is a CHILD's status that reaches the
# test below like any other. This is containment of the two shell builtins that
# end a process -- NOT a claim of immunity to arbitrary consumer values, which
# is not a property an eval boundary can have.
#
# Round 4's clamp reasoning is undisturbed: a child's 78 still lands in $PCT_RC
# with no provenance marker and still reaches the else branch (see the long note
# below). R5g in scripts/test-hooks.sh drives this BEHAVIOURALLY -- the source
# censuses in verify-template-consistency.sh cannot reach a value that arrives
# as config DATA rather than as hook SOURCE.
# v4.3.1 G1: that subshell now lives in pct_run_bounded, which also gives it its own process group and the budget.
pct_capture_tree
PCT_BUDGET=$(pct_budget "$REPO_PATH")
pct_run_bounded "$PCT_BUDGET" "$OUT" eval "$TEST_CMD"
if [ "$PCT_RC" = timeout ]; then
  pct_note test '"timeout"'
  pct_refuse_timeout "$PCT_BUDGET" "$TEST_CMD"
fi
# v3.0.3 diagnostic. Records the child's number as data; nothing here BRANCHES
# on it — see the long note below and census 21c-2h in
# scripts/verify-template-consistency.sh.
pct_note test "$PCT_RC"

# NO TERMINAL REMEDY AT THIS BOUNDARY (v2.2.5 round 4), and WHY THE TWO
# BOUNDARIES DIFFER.
#
# `$TEST_CMD` here is either a consumer's **Test** value or — when run-gate.sh is
# absent beside this hook — the raw **Gate** value. Both are ARBITRARY consumer
# commands, and 78 is EX_CONFIG, which real programs emit for their own reasons.
# Nothing stands between that command and this variable, so a 78 arriving here
# is always a CHILD's number, never a verdict any guard of ours reached.
# Forwarding it would hand a plain test failure the terminal remedy text — the
# INVERTED advice the conventions block in hooks/lib/git-cmd.sh forbids ("a hook
# NEVER forwards a child's exit code").
#
# The run-gate.sh boundary above needs no clamp for the opposite reason, and the
# asymmetry is not an oversight: run-gate.sh clamps its OWN gate command's 78
# internally, keyed on the provenance marker it created, so a 78 emerging from
# it has already been decided BY run-gate.sh to be its own terminal guard
# talking. There the number carries provenance; here it carries none.
#
# Residual edge, stated rather than engineered around: `**Test**: bash
# hooks/run-gate.sh` whose own **Gate** is self-referencing would produce a
# genuinely terminal 78 that this clamp demotes to a retryable one. That needs
# two pathologies at once, and attribution across an eval boundary this hook did
# not create would mean rebuilding a causal chain out of a file — the same
# disposition round 3 took for `run-gate.sh; some-other-tool`. The guard still
# prints its own specific remedy in the captured tail below; only the
# "cannot succeed as configured" framing is lost. run-gate.sh's other terminal
# guard is NOT reachable here at all: `$REPO_PATH` was resolved by gc_repo_for,
# so "not inside a git repository" cannot fire under this cd.
#
# SO THE FIX IS THE ABSENCE OF THE BRANCH, NOT A CLAMP ASSIGNMENT. Round 4 first
# wrote `PCT_RC=1` here as well. With the terminal arm gone that statement has NO
# observable effect — deleting it leaves every assertion green, which is exactly
# the guard-indistinguishable-from-its-absence shape this release refuses to
# ship. What is enforced instead is enforceable: the else branch below tests
# `$PCT_RC` against 0 and nothing else, and R5f in scripts/test-hooks.sh goes red
# the moment a terminal arm reappears here.
if [ "$PCT_RC" -eq 0 ]; then
  rm -f "$OUT"
  # The elapsed seconds are the ONLY external evidence the suite actually ran.
  # On success the captured output is deleted (right above) — correct, it is
  # noise on a green run — so "I saw no test output" is not evidence of a no-op.
  # A real suite takes minutes (616 s measured on a three-project repo); a hook
  # that fell through its own guards returns in about a second. One number
  # tells the two apart without reintroducing the noise.
  PCT_T1=$(date +%s 2>/dev/null || echo 0)
  echo "PRE-COMMIT: '$TEST_CMD' passed. ($((PCT_T1 - PCT_T0))s)" >&2
  exit 0
else
  # NO TERMINAL ARM HERE, DELIBERATELY (v2.2.5 round 4) — this absence IS the
  # fix, see the note above the success test. A 78 reaching this point is a
  # child's number with no provenance behind it, so branching on it would hand a
  # plain test failure the terminal remedy: inverted advice. Anyone restoring a
  # terminal arm must FIRST give this boundary a provenance channel; the number
  # alone cannot earn it. R5f in scripts/test-hooks.sh goes red if one returns.
  echo "BLOCKED: '$TEST_CMD' failed — re-run it and fix the failures before committing." >&2
  # Same reason as the run-gate.sh branch above: the escape hatch is named
  # where the block is read, not only in CLAUDE.md.
  echo "  (If the HOOK itself is broken rather than the suite: create '.claude/git-guard-off' under this cwd, make the one fix, then delete it. Never leave it in place.)" >&2
  # v3.0.3 (queue item 4, consumer-authored, verbatim), before the tail header
  # for the same reason as the run-gate branch above.
  echo "Could not determine: this check ran your suite against the WORKING TREE as it stood a moment ago, not against the tree this commit will contain. If only part of the tree is staged, or it changed between that run and this commit, the thing tested and the thing committed are different objects." >&2
  echo "--- last 20 lines ---" >&2
  tail -20 "$OUT" >&2
  rm -f "$OUT"
  exit 2
fi
