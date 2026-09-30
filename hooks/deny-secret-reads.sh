#!/usr/bin/env bash
# PreToolUse hook: refuse to read a secret-bearing .env file.
#
# Matcher: Read|Bash
#
# v4.1.2: this hook now calls cmd_join_continuations (hooks/lib/json.sh,
# already sourced below) on the Bash arm, so `awk` joins this hook's minimum
# PATH tool set alongside sh, sed, grep, tr — no test-hooks.sh fixture pins a
# restricted PATH for this hook specifically (see the task 1 report); noted
# here instead, per the brief's own fallback.
#
# WHY A HOOK AND NOT A DENY RULE (v3.0.3, queue item 28, measured from a
# consumer screenshot 2026-09-04). Six `Read(.env…)` entries used to sit in
# `permissions.deny` of every template `.claude/settings.json`. Claude Code
# evaluates deny rules BEFORE the auto-mode classifier, and a read-only command
# whose path is RELATIVE after a `cd` cannot be statically proven not to be a
# denied path — so the harness prompts ("a Read() deny rule is configured; only
# you can approve running it anyway") and auto mode cannot approve it. Subagents
# working in worktrees run `cd <worktree> && grep …` on nearly every read, so
# every "auto mode is prompting" report this cycle traced back to those six
# lines. A HOOK deny does not trigger the static path check: the hook sees the
# resolved call and answers per call. The protection is kept; the prompt is not.
#
# DECISION RULE
#   Read  : tool_input.file_path matches the secret shape          -> exit 2
#   Bash  : the command contains a READER VERB *and* a token that
#           matches the secret shape                               -> exit 2
#   anything else                                                  -> exit 0
#
# Secret shape: `(^|/)\.env(\.<suffix>)?$`, except a basename of exactly
# `.env.example` — that file is the documented placeholder list most repos
# track, and denying it was v2.2.0's BUG 6 (check 22b in
# verify-template-consistency.sh has guarded it ever since).
#
# WHY THE BASH ARM NEEDS A READER VERB, stated because the two obvious rules
# disagree: "any argv token that looks like a .env path" makes `echo .env` a
# denial, and `echo .env` is not a read — it is a literal, and the plan's own
# fixture list wants it allowed. Keying on the verb resolves that in the
# direction the fixtures state, and it keeps `git show HEAD:.env` out of this
# hook's jurisdiction for the right reason rather than by accident of the
# regex.
#
# v3.0.3 defect 2 — FOUR MEASURED HOLES INSIDE THE VERB MODEL, and the rule
# that was NOT adopted. A consumer measured `rev .env`, `dd if=.env`,
# `cp .env /dev/stdout` and `cat .e*v` all returning 0, and proposed re-keying
# the hook on "any argv token that names a secrets file, regardless of verb".
# That is the inverse rule this file's next paragraph already considered and
# REJECTED, for the reason stated there. It stays rejected. The holes are
# closed INSIDE the verb model instead:
#
#   1. `rev` was simply missing from the reader list. Added — the paragraph
#      below says adding verbs is cheap and safe, and this is that gap.
#   2. `dd` was already listed; the miss was the OPERAND SHAPE. `if=.env` is a
#      `key=value` token, not a bare path. Operands under a listed verb are now
#      also read out of `key=value`, but ONLY for INPUT-shaped keys (`if`, and
#      any key ending in `file`, `input` or `in`). Restricting the key list is
#      what keeps `grep -r ENV --exclude=.env .` allowed: `--exclude` is an
#      exclusion, not a read. STATED BLIND SPOT: an exotic input option whose
#      key is not on that list.
#   3. A COPY verb turned reader by its DESTINATION. `cp .env /dev/stdout`
#      copies the secret into the transcript. The copy class (`cp`, `install`,
#      `rsync`; `dd` and `tee` are already readers) is denied ONLY when the
#      destination is `/dev/stdout`, `/dev/fd/1` or `/dev/stderr`. A copy to a
#      FILE stays allowed, because it is a write: `cp .env.example .env` and
#      `cp .env /tmp/backup` are 0, asserted.
#   4. TRANSMIT verbs — `curl -T/--upload-file/-d @/--data-binary @`, `scp`,
#      `wget --post-file=` — send the contents off the machine, which is the
#      outcome the six deleted deny rules existed to prevent. They are reads by
#      another name. The auto-mode classifier's exfiltration rules also cover
#      them; this is belt-and-braces, said out loud rather than assumed.
#
# GLOB HEURISTIC, AND IT IS A HEURISTIC. `cat .e*v` cannot be resolved by a hook
# that does not know the working directory's contents, so the shape is judged
# instead of the target: under a LISTED verb, a token whose basename starts with
# `.e` and contains `*`, `?` or `[` is treated as secret-shaped. It over-matches
# (`cat .exports*` is denied) and under-matches (`cat *env`, `cat ?env` are
# not). Both are accepted: the verb requirement keeps the blast radius to
# commands that were going to print a file anyway.
#
# CASE. The match is case-INSENSITIVE, because the filesystems this runs on are:
# `cat .ENV` reads the same bytes as `cat .env`.
#
# THE VERB LIST IS AN ALLOWLIST AND IS THEREFORE KNOWN-INCOMPLETE — SAID HERE
# rather than discovered later, the same way v3.0.2's clause blocklist declared
# its own incompleteness. A reader that is not in the list below passes: today
# that is anything from `perl -ne`, `busybox cat` under another name, or a
# consumer's own script. Adding verbs is cheap and safe; the inverse rule ("deny
# any matching token unless the verb is provably inert") was considered and
# REJECTED, because it denies `cp .env.example .env`, `git add .env` and
# `rm .env`, none of which are reads. A missed read is a gap; a denied write is
# a guard people switch off.
#
# STATED BLIND SPOT. This hook sees a command's ARGUMENTS, not what an
# interpreter opens. `python -c "open('.env')"`, `node -e "fs.readFileSync…"`
# and `git show HEAD:.env` are NOT denied here — they are judged by the
# auto-mode classifier's own credential rules. Two fixtures assert exactly that
# (want 0) so the boundary is a decision on the record, not an oversight.
#
# FAILS CLOSED on an unparseable payload or with no JSON parser on PATH, the
# same posture as the three git gates that already share this matcher: a hook
# that cannot see the call cannot clear it.

lib="$(dirname "$0")/lib/json.sh"
[ -f "$lib" ] || { echo "BLOCKED: $lib missing — run /sync-template step 6b (hooks/lib/json.sh)" >&2; exit 2; }
# shellcheck source=lib/json.sh
. "$lib"

DSR_JSON=$(cat)

json_have || {
  echo "BLOCKED: deny-secret-reads: no JSON parser (node, python3 or jq) on PATH — this hook cannot inspect the call, and a call it cannot inspect is not one it can clear. Install one of the three." >&2
  exit 2
}
json_valid "$DSR_JSON" || {
  echo "BLOCKED: deny-secret-reads: hook payload did not parse — this hook cannot inspect the call. Report the payload; do not work around it." >&2
  exit 2
}

DSR_TOOL=$(json_get "$DSR_JSON" tool_name)

# The secret shape, as one place. Anchored at a path separator or the start, so
# `.environment` and `HEAD:.env` do not match, and `./.env` and `/x/.env.staging`
# do.
DSR_SECRET_RE='(^|/)\.env(\.[A-Za-z0-9_-][A-Za-z0-9_.-]*)?$'

dsr_is_secret() { # <path> -> 0 when it is a secret-bearing .env name
  dsr_b=${1##*/}
  dsr_lb=$(printf '%s' "$dsr_b" | tr 'A-Z' 'a-z')
  # EXPLICIT exemption, not an artifact of the anchoring: an exact `.env.example`
  # basename. `.env.examples` and `.env.sample` are NOT it, and are denied.
  [ "$dsr_lb" = ".env.example" ] && return 1
  printf '%s' "$1" | grep -qiE "$DSR_SECRET_RE" && return 0
  # The glob heuristic — see the header. `.e`-prefixed AND carrying a glob
  # metacharacter. `.environment` has no metacharacter and stays allowed.
  case "$dsr_lb" in
    .e*) case "$dsr_b" in *'*'*|*'?'*|*'['*) return 0 ;; esac ;;
  esac
  return 1
}

# dsr_paths_in <token> -- the path-shaped strings a token carries, one per line:
# the token itself with a leading `@` stripped (`curl -d @.env`), plus the value
# of an INPUT-shaped `key=value` (`if=`, `--file=`, `--post-file=`, `--input=`).
# Deliberately NOT every `key=value`: see hole 3 in the header for why
# `--exclude=.env` must stay allowed.
dsr_paths_in() {
  printf '%s\n' "${1#@}"
  case "$1" in
    *=*)
      dsr_k=$(printf '%s' "${1%%=*}" | tr 'A-Z' 'a-z')
      case "$dsr_k" in
        if|*file|*input|*in) dsr_v=${1#*=}; printf '%s\n' "${dsr_v#@}" ;;
      esac ;;
  esac
}

dsr_deny() { # <path> <location-desc> [extra-clause]
  {
    echo "BLOCKED: '$1' — secrets files are not read by agents. (matched: \"$1\" in $2)"
    echo "  Could not determine: this hook sees the command's arguments, not what an interpreter opens; 'python -c open(...)' and 'git show HEAD:.env' are judged by the auto-mode classifier, not here."
    [ -n "${3:-}" ] && printf '%s\n' "$3" | sed 's/^/  /'
    echo "  If you need a variable's NAME, read '.env.example' — it is allowed on purpose."
    echo "  Inspecting a config file that merely mentions one? Read it with the Read tool."
  } >&2
  exit 2
}

case "$DSR_TOOL" in
  Read)
    DSR_PATH=$(json_get "$DSR_JSON" tool_input.file_path)
    [ -n "$DSR_PATH" ] || exit 0
    dsr_is_secret "$DSR_PATH" && dsr_deny "$DSR_PATH" "the file path argument"
    ;;
  Bash|PowerShell)
    DSR_CMD=$(json_get "$DSR_JSON" tool_input.command)
    # v4.1.2 spec §1: this hook never sourced git-cmd.sh, so the continuation
    # join lives in json.sh (already sourced above, fail-closed). Without it
    # `cat .e\<LF>nv` read as two tokens and the .env read was ALLOWED.
    # Raw-text fallback: an empty DSR_CMD hits `[ -n "$DSR_CMD" ] || exit 0`
    # (:170) -- a failed join must never turn into allow-everything.
    if [ -n "$DSR_CMD" ]; then
      _dsr_j=$(printf '%s' "$DSR_CMD" | cmd_join_continuations) && [ -n "$_dsr_j" ] && DSR_CMD="$_dsr_j"
    fi
    [ -n "$DSR_CMD" ] || exit 0
    # Split on whitespace and on the shell operators that begin a new command,
    # then judge tokens. Quotes are stripped so `grep KEY "$PWD/.env.local"`
    # presents the same token shape as the unquoted form.
    DSR_TOKENS=$(printf '%s' "$DSR_CMD" | tr '|;&()<>' '\n' | tr -s '[:space:]' '\n' | tr -d '"'"'")
    DSR_READER=0   # a verb that prints a file's contents
    DSR_COPY=0     # a copy verb: a read only when the destination is a std stream
    DSR_XMIT=0     # a transmit verb: sends the contents off the machine
    DSR_STDOUT=0   # a std-stream destination is present in this command
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      case "${tok##*/}" in
        cat|bat|head|tail|less|more|nl|od|xxd|hexdump|strings|sed|awk|gawk|grep|egrep|fgrep|rg|ag|cut|tr|sort|uniq|wc|tee|dd|base64|jq|yq|diff|cmp|envsubst|readarray|mapfile|source|rev|.)
          DSR_READER=1 ;;
        cp|install|rsync) DSR_COPY=1 ;;
        curl|scp|wget|sftp) DSR_XMIT=1 ;;
      esac
      case "$tok" in
        /dev/stdout|/dev/stderr|/dev/fd/1|/dev/fd/2) DSR_STDOUT=1 ;;
      esac
    done <<DSR_TOK
$DSR_TOKENS
DSR_TOK
    # A copy verb is a READ only when it copies into a standard stream, i.e.
    # into the transcript. `cp .env /tmp/backup` is a write and stays allowed.
    [ "$DSR_COPY" -eq 1 ] && [ "$DSR_STDOUT" -eq 1 ] && DSR_READER=1
    [ "$DSR_XMIT" -eq 1 ] && DSR_READER=1
    [ "$DSR_READER" -eq 1 ] || exit 0
    DSR_ARGN=0
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      DSR_ARGN=$((DSR_ARGN + 1))
      while IFS= read -r dsr_p; do
        [ -n "$dsr_p" ] || continue
        if dsr_is_secret "$dsr_p"; then
          dsr_deny "$dsr_p" "argument $DSR_ARGN" "Matched a reader verb and a secrets filename in the same command, though not necessarily in the same clause: a mention in an echo or a comment counts, and so does a reader that never touches the file. Whole-command matching is deliberate, so that a wrapper such as \`bash -c \"...\"\` cannot hide the read."
        fi
      done <<DSR_PATHS
$(dsr_paths_in "$tok")
DSR_PATHS
    done <<DSR_TOK2
$DSR_TOKENS
DSR_TOK2
    ;;
esac

exit 0
