#!/usr/bin/env bash
# PostToolUse hook: truncate oversized Bash/PowerShell output before it lands
# in the transcript, keeping the full text on disk.
#
# Matcher: Bash|PowerShell
#
# Measured payload shape (Claude Code 2.1.250, observed via a temporary logging
# hook on 2026-08-28):
#   { session_id, transcript_path, cwd, prompt_id, permission_mode, agent_id,
#     agent_type, effort:{level}, hook_event_name:"PostToolUse", tool_name,
#     tool_input:{command,...}, tool_use_id, duration_ms,
#     tool_response: { stdout, stderr, interrupted, isImage,
#                      noOutputExpected } }
# `stdout` is the output string field. updatedToolOutput must keep the same
# shape, so the whole tool_response object is copied and only stdout replaced.
#
# Behaviour: both `stdout` and `stderr` are checked independently. A stream at
# or under THRESHOLD chars is untouched; a longer one is written whole to
# $TMPDIR/claude-bash-out/ (one log per stream) and replaced by head + a
# truncation marker naming the log file + tail. No stream over the threshold
# means no output at all.
#
# The payload is piped to node on STDIN, never passed in argv: Linux caps a
# single argument at 128 KiB and Windows CreateProcess caps the whole command
# line at 32,767 chars, so an argv-passed 200 KB build log — precisely the case
# this hook exists for — would fail to exec and slip through untruncated.
#
# Always exits 0. A PostToolUse hook that fails must never disturb the tool
# result, so every error path is a silent pass-through — with ONE exception as
# of v2.2.0: a host with no `node` gets a single WARN line on stderr (once per
# hook per TMPDIR, see json_warn_once) rather than silence, because a truncation
# guard that has quietly stopped running is indistinguishable from one that has
# nothing to truncate.

THRESHOLD=12000
KEEP=4000

TOOL_INPUT=$(cat)

# v4.4.0 C4: the engine below prints only when tool_response.stdout or
# tool_response.stderr (the two fields it measures, both inside the payload) is
# longer than THRESHOLD UTF-16 units; any other path is silent. Each unit costs at
# least one byte of the JSON text (BMP: 1-3 bytes, or 2-6 escaped; astral: 4 bytes
# or 12 escaped for 2 units), so a payload of <= THRESHOLD bytes cannot hold such an
# output. Side effect: a small payload no longer reaches the once-per-TMPDIR no-node
# WARN below; the decision (no output, exit 0) is the same.
_bog_set=${LC_ALL+x}; _bog_lc=${LC_ALL-}
LC_ALL=C; _bog_n=${#TOOL_INPUT}
if [ -n "$_bog_set" ]; then LC_ALL=$_bog_lc; else unset LC_ALL; fi
[ "$_bog_n" -le "$THRESHOLD" ] && exit 0

# v2.2.0: the spill/truncate engine below is an embedded node program, so this
# hook needs node specifically. Without it it stays fail-open, but says so once.
jlib="$(dirname "$0")/lib/json.sh"
if [ -f "$jlib" ]; then
  . "$jlib"
  json_require_node bash-output-guard "$(json_session "$TOOL_INPUT")" || exit 0
fi
command -v node >/dev/null 2>&1 || exit 0

OUTDIR="${TMPDIR:-/tmp}/claude-bash-out"
mkdir -p "$OUTDIR" 2>/dev/null || exit 0

printf '%s' "$TOOL_INPUT" | node -e '
try {
  var fs = require("fs");
  var path = require("path");
  var payload = JSON.parse(fs.readFileSync(0, "utf8"));
  var outDir = process.argv[1];
  var threshold = Number(process.argv[2]);
  var keep = Number(process.argv[3]);

  var resp = payload.tool_response;
  if (!resp || typeof resp !== "object") process.exit(0);

  var updated = Object.assign({}, resp);
  var changed = false;
  var stamp = Date.now();

  ["stdout", "stderr"].forEach(function (field) {
    var out = resp[field];
    if (typeof out !== "string" || out.length <= threshold) return;

    var suffix = field === "stderr" ? "-stderr" : "";
    var logPath = path.join(outDir,
      (payload.session_id || "nosession") + "-" + stamp + suffix + ".log");
    fs.writeFileSync(logPath, out);

    var dropped = out.length - keep * 2;
    updated[field] = out.slice(0, keep) +
      "\n…[" + dropped + " chars truncated — full " +
      (field === "stderr" ? "stderr" : "output") + ": " + logPath + "]…\n" +
      out.slice(out.length - keep);
    changed = true;
  });

  if (!changed) process.exit(0);

  console.log(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      updatedToolOutput: updated
    }
  }));
} catch (e) {}
' "$OUTDIR" "$THRESHOLD" "$KEEP" 2>/dev/null

exit 0
