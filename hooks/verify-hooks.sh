#!/usr/bin/env bash
# hooks/verify-hooks.sh [--report]
#
# SessionStart hook (no matcher) + the doctor's reader. v4.4.0 C2.
#
# A hook registration that points at a script that is gone is not a loud failure
# for the protections that are registered fail-closed (those exit 2), but it IS
# silent for every other one, and an exec-form registration whose PROGRAM cannot
# be spawned is a non-blocking error in Claude Code: every check behind it passes.
# So once per session, look at every registration this machine will use and say so.
#
# Reads: $HOME/.claude/settings.json, and $CLAUDE_PROJECT_DIR/.claude/settings.json
# and settings.local.json (project files only when CLAUDE_PROJECT_DIR is set).
# Reports:
#   MISSING: <script>            registered script absent or unreadable
#   BROKEN: <script>             `bash -n` fails on it
#   MISSING PROGRAM: <cmd> ...   an exec-form (has `args`) `command` that cannot be spawned
#   MISSING (unrendered @...@)   an @NAME@ placeholder left in a settings file
#   NO PARSER: ...               exec-form entries could not be read
#
# Default mode (SessionStart): plain text on stdout is injected into context. One
# block when anything is wrong, nothing when all is well. ALWAYS exit 0.
# --report: the problem lines only, exit 1 when there are any (doctor, drift script).
#
# FAIL-OPEN BY CONSTRUCTION: a diagnostic never blocks. Cost: one grep per settings
# file (plus one parser run for a file that has exec-form entries) and one `bash -n`
# per distinct script. Sourced (`. "$0"`) or run (`bash x.sh`): no top-level return.

MODE=default
[ "${1:-}" = "--report" ] && MODE=report

VH_PROJ=${CLAUDE_PROJECT_DIR:-}
VH_HOME=${HOME:-}
VH_PROBS=""
VH_N=0
VH_PROGFAIL=
VH_NOPARSER=
VH_SEEN="
"

vh_problem() { VH_N=$((VH_N + 1)); VH_PROBS="$VH_PROBS$1
"; }

# vh_resolve <path> -> sets VH_R to the absolute-or-project-relative path; returns 1 when it cannot be resolved here
# (no command substitution: a subshell per path occurrence was the cost)
vh_resolve() {
  vh_p=$1
  vh_a='${CLAUDE_PROJECT_DIR:-.}'; vh_p=${vh_p//"$vh_a"/${VH_PROJ:-.}}
  vh_a='${CLAUDE_PROJECT_DIR}';    vh_p=${vh_p//"$vh_a"/${VH_PROJ:-.}}
  vh_a='$CLAUDE_PROJECT_DIR';      vh_p=${vh_p//"$vh_a"/${VH_PROJ:-.}}
  vh_a='${HOME}';                  vh_p=${vh_p//"$vh_a"/$VH_HOME}
  vh_a='$HOME';                    vh_p=${vh_p//"$vh_a"/$VH_HOME}
  case $vh_p in "~/"*) vh_p=$VH_HOME/${vh_p#"~/"} ;; esac
  case $vh_p in
    *[\$\*\'@\{\}\"\\]*|"") return 1 ;;
    /*|[A-Za-z]:/*|./*|../*) ;;
    *) vh_p=${VH_PROJ:-.}/$vh_p ;;
  esac
  VH_R=$vh_p
}

# vh_script <raw path> -- check one registered script (once)
vh_script() {
  case $1 in */run-gate.sh|hooks/run-gate.sh) return 0 ;; esac   # a permission pattern, not a registration
  vh_resolve "$1" || return 0
  vh_r=$VH_R
  case "$VH_SEEN" in *"
$vh_r
"*) return 0 ;; esac
  VH_SEEN="$VH_SEEN$vh_r
"
  if [ ! -f "$vh_r" ] || [ ! -r "$vh_r" ]; then vh_problem "MISSING: $vh_r"; return 0; fi
  bash -n "$vh_r" >/dev/null 2>&1 || vh_problem "BROKEN: $vh_r (bash -n failed)"
  return 0
}

# vh_program <command> -- an exec-form command program must exist and be executable
vh_program() {
  # an @NAME@ placeholder is reported on its own; an empty command is a program that cannot be spawned
  if [ -n "$1" ] && [[ $1 =~ @[A-Z]+@ ]]; then return 0; fi
  vh_c=$1
  if [ -z "$vh_c" ]; then
    VH_PROGFAIL=1
    vh_problem "MISSING PROGRAM: (empty command) (exec-form hook command cannot be spawned -- every check it runs is OFF; re-run scripts/render-user-hooks.sh --write)"
    return 0
  fi
  case $vh_c in
    */*|[A-Za-z]:*) ;;
    *) vh_c=$(command -v "$vh_c" 2>/dev/null) || vh_c=$1 ;;
  esac
  if [ -f "$vh_c" ] && [ -x "$vh_c" ]; then return 0; fi
  case $vh_c in *.exe|*.EXE) ;; *) if [ -f "$vh_c.exe" ] && [ -x "$vh_c.exe" ]; then return 0; fi ;; esac
  VH_PROGFAIL=1
  vh_problem "MISSING PROGRAM: $1 (exec-form hook command cannot be spawned -- every check it runs is OFF; re-run scripts/render-user-hooks.sh --write)"
  return 0
}

# vh_entries <file> <backend>: one line per hook object: E<US>command<US>last arg | S<US>command | P (does not parse)
vh_entries() {
  case $2 in
    node)
      node -e '
        var t; try { t = require("fs").readFileSync(0, "utf8"); if (t.charCodeAt(0) === 0xFEFF) t = t.slice(1); t = JSON.parse(t); }
        catch (e) { process.stdout.write("P\n"); process.exit(0); }
        function c(x) { return String(x).replace(/[\t\r\n\u001f]/g, " "); }
        var out = [], h = t && t.hooks;
        if (h && typeof h === "object") Object.keys(h).forEach(function (ev) {
          if (!Array.isArray(h[ev])) return;
          h[ev].forEach(function (g) {
            if (!g || !Array.isArray(g.hooks)) return;
            g.hooks.forEach(function (x) {
              if (!x || typeof x !== "object") return;
              if (Array.isArray(x.args)) out.push("E\u001f" + c(x.command === undefined || x.command === null ? "" : x.command) + "\u001f" + c(x.args.length ? x.args[x.args.length - 1] : ""));
              else if (typeof x.command === "string") out.push("S\u001f" + c(x.command));
            });
          });
        });
        process.stdout.write(out.join("\n") + (out.length ? "\n" : ""));
      ' < "$1" 2>/dev/null ;;
    python3)
      python3 -c '
import json, sys
try:
    t = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
except Exception:
    sys.stdout.write("P\n"); sys.exit(0)
def c(x):
    x = str(x)
    for ch in "\t\r\n\x1f": x = x.replace(ch, " ")
    return x
out = []
h = t.get("hooks") if isinstance(t, dict) else None
if isinstance(h, dict):
    for ev, gs in h.items():
        if not isinstance(gs, list): continue
        for g in gs:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list): continue
            for x in g["hooks"]:
                if not isinstance(x, dict): continue
                cmd = x.get("command")
                if isinstance(x.get("args"), list):
                    a = x["args"]
                    out.append("E\x1f" + c("" if cmd is None else cmd) + "\x1f" + c(a[-1] if a else ""))
                elif isinstance(cmd, str):
                    out.append("S\x1f" + c(cmd))
sys.stdout.buffer.write(("".join(l + "\n" for l in out)).encode("utf-8"))
' < "$1" 2>/dev/null ;;
    jq)
      # jq.exe writes text-mode CRLF; -b (jq >= 1.7) keeps LF so a trailing CR never lands in a path
      vh_jb=; jq -b -n 1 >/dev/null 2>&1 && vh_jb=-b
      jq $vh_jb -r '
        def c: tostring | gsub("[\t\r\n\u001f]"; " ");
        (.hooks // {}) | if type == "object" then
          .[] | arrays[] | objects | (.hooks // []) | arrays[] | objects
          | if (.args | type) == "array" then "E\u001f" + ((.command // "") | c) + "\u001f" + ((.args | if length > 0 then .[length - 1] else "" end) | c)
            elif (.command | type) == "string" then "S\u001f" + (.command | c)
            else empty end
        else empty end' < "$1" 2>/dev/null || printf 'P\n' ;;
  esac
}

vh_file() {
  [ -f "$1" ] && [ -r "$1" ] || return 0
  if grep -Eq '@[A-Z]+@' "$1" 2>/dev/null; then
    vh_problem "MISSING (unrendered @...@ -- run scripts/render-user-hooks.sh --write): $1"
  fi
  if ! grep -q '"args"' "$1" 2>/dev/null; then
    # shell-form only: over-collect hook paths by name, like sync-template rule 1b
    vh_paths=$(grep -oE '[^"[:space:]]*hooks/[A-Za-z0-9_.-]+\.sh' "$1" 2>/dev/null)
    while IFS= read -r vh_x; do [ -n "$vh_x" ] && vh_script "$vh_x"; done <<EOF
$vh_paths
EOF
    return 0
  fi
  vh_init_parser
  if [ -z "$VH_BACKEND" ]; then
    VH_NOPARSER=1
    vh_problem "NO PARSER: exec-form entries unchecked in $1"
    return 0
  fi
  { vh_ents=$(vh_entries "$1" "$VH_BACKEND"); } 2>/dev/null
  vh_us=$(printf '\037')
  while IFS= read -r vh_line; do
    case $vh_line in
      P) vh_problem "BROKEN: $1 (settings JSON does not parse)" ;;
      E"$vh_us"*)
        vh_rest=${vh_line#E"$vh_us"}; vh_cmd=${vh_rest%%"$vh_us"*}; vh_last=${vh_rest#*"$vh_us"}
        vh_program "$vh_cmd"
        case $vh_last in *.sh) vh_script "$vh_last" ;; esac ;;
      S"$vh_us"*)
        vh_rest=${vh_line#S"$vh_us"}
        vh_paths=$(printf '%s\n' "$vh_rest" | grep -oE '[^"[:space:]]*hooks/[A-Za-z0-9_.-]+\.sh' 2>/dev/null)
        while IFS= read -r vh_x; do [ -n "$vh_x" ] && vh_script "$vh_x"; done <<EOF
$vh_paths
EOF
        ;;
    esac
  done <<EOF
$vh_ents
EOF
  return 0
}

# the parser is only needed for a file that has exec-form entries: initialise it on first use
VH_BACKEND=""
VH_PARSER_DONE=""
vh_init_parser() {
  [ -z "$VH_PARSER_DONE" ] || return 0
  VH_PARSER_DONE=1
  vh_jlib="$(dirname "$0")/lib/json.sh"
  if [ -f "$vh_jlib" ]; then
    . "$vh_jlib"
    json_parser_init
    [ "$JSON_PARSER" = "none" ] || VH_BACKEND=$JSON_PARSER
  fi
}

[ -n "$VH_HOME" ] && vh_file "$VH_HOME/.claude/settings.json"
if [ -n "$VH_PROJ" ]; then
  vh_file "$VH_PROJ/.claude/settings.json"
  vh_file "$VH_PROJ/.claude/settings.local.json"
fi

if [ "$VH_N" -gt 0 ]; then
  if [ "$MODE" = report ]; then
    printf '%s' "$VH_PROBS"
    exit 1
  fi
  echo "HOOK CHECK FAILED -- $VH_N registered hook script(s) missing or broken:"
  printf '%s' "$VH_PROBS"
  echo "Tell the user this in your first reply, before anything else. Protections stay fail-closed (a missing protection blocks its tool calls); fix with /sync-template or re-run scripts/render-user-hooks.sh --write."
  if [ -n "$VH_PROGFAIL" ] && [ -n "$VH_NOPARSER" ]; then
    echo "A MISSING PROGRAM entry fails OPEN, and NO PARSER means exec-form entries are unchecked: every check behind them may be off until fixed."
  elif [ -n "$VH_PROGFAIL" ]; then
    echo "A MISSING PROGRAM entry fails OPEN: every check behind it is off until it is fixed."
  elif [ -n "$VH_NOPARSER" ]; then
    echo "NO PARSER means exec-form entries are unchecked: every check behind them may be off until a parser (node, python3 or jq) is available."
  fi
fi
exit 0
