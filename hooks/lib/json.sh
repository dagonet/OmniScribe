#!/usr/bin/env bash
# hooks/lib/json.sh
#
# One JSON field reader for every enforcement hook.
#
# Why: until v2.2.0 the hooks parsed their stdin payload with `node -e`. Native
# Claude Code installs (and most WSL/Linux boxes that never installed a JS
# toolchain) have no `node`, so every `node -e` returned empty, every gate saw
# an empty command and exited 0 — the guards were silently inactive. Reported
# 2026-08-29 from a WSL dry run.
#
# Backends, in order: node, python3, jq. All three read the payload from STDIN
# (never argv, which has platform length caps) and print the value with no
# trailing newline.
#
# v2.2.1: a backend is PROBED before it is trusted (json_probe_ok). Being on
# PATH is not evidence that a thing works — Windows ships a non-functional
# `python3` stub on PATH by default — and a broken parser was indistinguishable
# from an absent field, which put the git gates back on the fail-OPEN path the
# whole of v2.2.0 exists to close.
#
# Source it from a hook:  . "$(dirname "$0")/lib/json.sh"

JSON_PARSER=""

# A UTF-8 BOM. Claude Code does not emit one, but a payload that has been
# round-tripped through a Windows tool can carry it, and it makes every backend
# fail to parse — stripped once here rather than three times below.
JSON_BOM=$(printf '\357\273\277')

# json_read <backend> <json> <dotted.path> -- the per-backend field read.
# Not called directly by hooks; json_get picks the backend. Split out of
# json_get in v2.2.1 so json_parser can PROBE a backend before trusting it.
json_read() {
  case "$1" in
    node)
      printf '%s' "$2" | node -e '
        var v; try { v = JSON.parse(require("fs").readFileSync(0, "utf8")); }
        catch (e) { process.exit(0); }
        var p = process.argv[1].split(".");
        for (var i = 0; i < p.length; i++) {
          if (v === null || typeof v !== "object") { v = undefined; break; }
          v = v[p[i]];
        }
        if (v === undefined || v === null || typeof v === "object") process.exit(0);
        process.stdout.write(String(v));
      ' "$3" 2>/dev/null
      ;;
    python3)
      # Bytes in, bytes out, UTF-8 both ways. `json.load(sys.stdin)` decodes in
      # the LOCALE encoding: on Windows Git Bash (ANSI code page) or under
      # LC_ALL=C an em dash in the payload raises UnicodeDecodeError, the field
      # comes back empty, and a gate that keys on it exits 0 silently. The
      # matching sys.stdout.write would raise UnicodeEncodeError on the way out.
      printf '%s' "$2" | python3 -c '
import json, sys
try:
    v = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace"))
except Exception:
    sys.exit(0)
for k in sys.argv[1].split("."):
    if not isinstance(v, dict):
        v = None
        break
    v = v.get(k)
if v is None or isinstance(v, (dict, list)):
    sys.exit(0)
sys.stdout.buffer.write((v if isinstance(v, str) else json.dumps(v)).encode("utf-8"))
' "$3" 2>/dev/null
      ;;
    jq)
      printf '%s' "$2" | jq -j --arg p "$3" '
        getpath($p | split("."))
        | if . == null or type == "object" or type == "array" then ""
          elif type == "string" then .
          else tojson end
      ' 2>/dev/null
      ;;
    *) printf '' ;;
  esac
}

# json_probe_ok <backend> -- v2.2.1: does this backend actually WORK?
#
# `command -v` is not enough. Windows ships an App-Installer STUB at
# %LOCALAPPDATA%/Microsoft/WindowsApps/python3 that is on PATH by default and
# is not an interpreter; conda/pyenv shims, a half-installed node and a jq
# built without a working locale fail the same way. json_get then returns ""
# for every field, the git gates read an empty command, and they exit 0 — the
# fail-OPEN outcome PR15's fail-closed design exists to prevent, on the exact
# platform most of this toolkit's users are on. A missing parser is detected; a
# broken one was not.
#
# The probe is a nested read, not just "did it run": a backend that executes
# but extracts the wrong thing is no more usable than one that crashes.
JSON_PROBE='{"jp":{"k":"ok"}}'
json_probe_ok() {
  [ "$(json_read "$1" "$JSON_PROBE" jp.k)" = "ok" ]
}

# json_parser_init -- resolve the backend ONCE, into the CURRENT shell.
#
# Memoisation only works if the assignment survives, and it does not survive a
# command substitution. `json_parser()` is almost always called as
# `$(json_parser)`, so the probe ran in a throwaway subshell and `JSON_PARSER`
# was empty again on the next call — roughly five probe forks per git-gate run
# on a Bash|PowerShell matcher, not the "one, on first use" the comment claimed.
# Cheap while the probe was a `command -v` builtin; not cheap now that it execs
# an interpreter. So the callers below call THIS in their own shell and read
# $JSON_PARSER, and `json_parser` stays as the printing wrapper for the few
# places that want the name. gc_read_stdin's json_have call runs in the hook's
# top-level shell, so the later `$(json_get …)` subshells inherit the resolved
# value and the whole gate costs one probe.
json_parser_init() {
  [ -n "$JSON_PARSER" ] && return 0
  JSON_PARSER=none
  for jsb in node python3 jq; do
    command -v "$jsb" >/dev/null 2>&1 || continue
    json_probe_ok "$jsb" || continue
    JSON_PARSER=$jsb
    break
  done
  return 0
}

# json_parser -- prints node | python3 | jq | none.
json_parser() { json_parser_init; printf '%s' "$JSON_PARSER"; }

# json_have -- true when a WORKING backend is available.
json_have() { json_parser_init; [ "$JSON_PARSER" != "none" ]; }

# json_valid <json> -- true when the payload parses as JSON.
#
# v2.2.1: json_get returns "" both for "field absent" and for "the payload is
# not JSON", and the git gates read that as "no command here, allow". A gate
# that cannot determine the answer must refuse, so the callers need to tell the
# two apart. Empty stdin is deliberately INVALID here, not an empty document.
json_valid() {
  case "$1" in "$JSON_BOM"*) set -- "${1#"$JSON_BOM"}" ;; esac
  [ -n "$1" ] || return 1
  json_parser_init
  case "$JSON_PARSER" in
    node)
      printf '%s' "$1" | node -e '
        try { JSON.parse(require("fs").readFileSync(0, "utf8")); }
        catch (e) { process.exit(1); }
      ' 2>/dev/null
      ;;
    python3)
      printf '%s' "$1" | python3 -c '
import json, sys
try:
    json.loads(sys.stdin.buffer.read().decode("utf-8-sig", "replace"))
except Exception:
    sys.exit(1)
' 2>/dev/null
      ;;
    jq)
      printf '%s' "$1" | jq -e . >/dev/null 2>&1
      ;;
    *) return 1 ;;
  esac
}

# json_get <json> <dotted.path> -- prints the scalar at that path, or "".
#
# Objects, arrays, null and missing keys all print "" — the hooks treat an
# unreadable field as absent, which is the same contract the old `node -e ||
# echo ''` calls had. Callers that must not conflate "absent" with "unparseable"
# check json_valid first.
json_get() {
  case "$1" in "$JSON_BOM"*) set -- "${1#"$JSON_BOM"}" "$2" ;; esac
  json_parser_init
  json_read "$JSON_PARSER" "$1" "$2"
}

# json_session <json> -- the payload's session_id, by grep. Deliberately NOT
# json_get: the warn-once path has to work when there is no parser at all.
json_session() {
  printf '%s' "$1" | grep -o '"session_id":"[^"]*"' | head -1 | cut -d'"' -f4
}

# json_warn_once <hook-name> <session-id> <message> -- print <message> to stderr
# at most once per hook per session. PreToolUse hooks fire on EVERY tool call,
# so an unconditional warning is thousands of identical stderr lines; a marker
# with no session in it is the opposite failure — on a host-global TMPDIR the
# hook would warn once EVER and every later outage would be silent. With no
# session id to key on (a payload we could not even grep) the marker expires
# after an hour instead. Best-effort: an unwritable marker just means the
# warning repeats. Never changes an exit code.
#
# The session id lands in a FILENAME, so a value carrying a path separator or
# `..` is discarded rather than sanitised — an unusable key is exactly the
# no-session case, and the TTL path below is the right fallback for it.
JSON_WARN_TTL=3600
json_warn_once() {
  jws=$2
  case "$jws" in */*|*\\*|*..*) jws="" ;; esac
  jwm="${TMPDIR:-/tmp}/claude-hook-warn-$1${jws:+-$jws}"
  if [ -f "$jwm" ]; then
    [ -n "$jws" ] && return 0
    jwmt=$(stat -c %Y "$jwm" 2>/dev/null || stat -f %m "$jwm" 2>/dev/null || echo 0)
    [ $(( $(date +%s) - jwmt )) -lt "$JSON_WARN_TTL" ] && return 0
  fi
  : > "$jwm" 2>/dev/null || true
  echo "$3" >&2
}

# json_warn_no_parser <hook-name> [session-id] -- the ONE stderr line a fail-open
# hook prints when it cannot enforce anything. Never changes an exit code.
json_warn_no_parser() {
  json_warn_once "$1" "${2:-}" "WARN: $1: no JSON parser on PATH — enforcement inactive"
}

# json_require_node <hook-name> [session-id] -- for the fail-open hooks whose
# engine is an embedded node program (a JSONL transcript scan, a JSON rewrite)
# rather than a field read. Returns non-zero — and warns once per session — when
# node is missing, so the caller can `|| exit 0`.
#
# v2.2.1: this is the SECOND entry point a broken interpreter reaches, so it
# runs the same probe as json_parser rather than a bare `command -v node`. A
# node that is on PATH but does not execute would otherwise pass here, the
# embedded program would produce nothing, and the hook would fail open in
# silence — exactly the case json_probe_ok exists for.
json_require_node() {
  if command -v node >/dev/null 2>&1 && json_probe_ok node; then
    return 0
  fi
  if json_have; then
    json_warn_once "$1" "${2:-}" "WARN: $1: node not usable (found $(json_parser)) — enforcement inactive"
  else
    json_warn_no_parser "$1" "${2:-}"
  fi
  return 1
}

# cmd_join_continuations -- stdin -> stdout. Deletes every backslash-newline
# pair (optional CR before the LF) whose run of preceding backslashes is ODD,
# i.e. exactly what POSIX sh does before it tokenises: `git pu\<LF>sh` is the
# verb `push`, and `a\\<LF>` is `a\` followed by a real command boundary.
#
# WHY THIS LIVES IN json.sh AND NOT git-cmd.sh OR A NEW FILE (v4.1.2, outside
# reviewer, measured): every command-reading hook already sources this file --
# including the user-level, machine-wide, fail-closed deny-secret-reads.sh,
# where a NEW sourced dependency is a new propagation point whose one missed
# copy blocks every Bash call on the machine (with the guard) or silently
# skips the join (without it). Sourcing git-cmd.sh instead costs ~57 ms per
# call machine-wide from a hook that never runs git. One definition here: zero
# new surface, ~zero cost. The name is neutral on purpose.
cmd_join_continuations() {
  # The input's trailing newline (present or not) is preserved EXACTLY: the
  # sync-template skill's step-3 probe asserts cmd_len == len(invocation) + 1
  # + len(body), and a join that added or dropped one byte would move that
  # figure -- the v4.1.1 #3 correction was about precisely this byte.
  local _cj_in _cj_trail=0 _cj_nl
  # v4.1.2 fix (task 1 verification): $(printf '\n') strips its own trailing
  # newline like any command substitution, so this used to assign EMPTY --
  # `case "$_cj_in" in *"$_cj_nl")` then matched *every* string (the empty
  # pattern), so _cj_trail was always 1 and a newline was appended even to
  # input with none. Same guard-byte idiom as the next line fixes it: append
  # a marker, let the substitution strip only ITS trailing newline, then trim
  # the marker back off, leaving the newline character intact in the var.
  _cj_nl=$(printf '\nx'); _cj_nl=${_cj_nl%x}
  _cj_in=$(cat; printf x); _cj_in=${_cj_in%x}
  case "$_cj_in" in *"$_cj_nl") _cj_trail=1 ;; esac
  # v4.1.2 fix round 2 (outside reviewer, measured): on Windows, gawk opens
  # stdin in TEXT MODE and translates CRLF to LF before the program ever
  # sees $0, so the /\r$/ branch below never matches and CR is stripped from
  # EVERY line, not just continuation lines -- silently breaking the
  # documented "CR kept on ordinary lines" contract on this platform only.
  # -v BINMODE=3 makes gawk read stdin/stdout as raw bytes, so the CR
  # survives into $0 and the existing branch handles it as designed; mawk
  # and BSD awk ignore an unknown BINMODE variable and never did text-mode
  # translation in the first place, so this is a no-op there.
  printf '%s' "$_cj_in" | awk -v BINMODE=3 'BEGIN{ORS=""; pend=""; first=1}
  {
    line=$0; cr=""
    if (line ~ /\r$/) { cr="\r"; line=substr(line,1,length(line)-1) }
    n=0; i=length(line)
    while (i>0 && substr(line,i,1)=="\\") { n++; i-- }
    if (n % 2 == 1) { pend=pend substr(line,1,length(line)-1) }
    else { if (!first) print "\n"; print pend line cr; pend=""; first=0 }
  }
  END{ if (pend!="") { if (!first) print "\n"; print pend } }'
  [ "$_cj_trail" = 1 ] && printf '\n'
  return 0
}
