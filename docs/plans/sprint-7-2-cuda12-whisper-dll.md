# OmniScribe — Sprint 7.2: CUDA-12 / faster-whisper Windows DLL alignment

**Goal:** Finish the CUDA-12 alignment that `681fa03` started for ONNXRuntime — extend the same bundled-wheel + DLL-preload approach to faster-whisper / CTranslate2 so Windows users without a system CUDA-12 install can run Whisper inference out of the box.

## Context

After v0.1.0 (`10d641a`, 2026-04-25), `681fa03` aligned **onnxruntime-gpu** with CUDA-12 on Windows. The faster-whisper / CTranslate2 side was left unfinished — Windows users without a system CUDA-12 install hit `LoadLibrary("cublas64_12.dll")` failures at first Whisper inference.

WIP already in the working tree (uncommitted, branch `feat/sprint-7-2-cuda12-whisper-dll`):
- `pyproject.toml` — `+ "nvidia-cuda-runtime-cu12>=12"` direct dep
- `src/omniscribe/asr/whisper.py` — Windows-only DLL shim (`_register_nvidia_dll_dirs`) at module import
- `uv.lock` — partial entries for the new dep

This sprint hardens the WIP into a shippable change.

## Tier

**T3** — `python-coder` + `code-reviewer` + `tester`. Multi-file change, < 200 LOC, behavior-critical (silent failure on Windows GPU users). Reviewer + tester earn their keep.

## Why a shim is needed

`nvidia-*` pip packages ship as namespace packages without `__init__.py`, so their `bin/` directories are never added to the Windows DLL search path. `os.add_dll_directory()` alone is not sufficient because the loader does not always walk all registered directories when chasing transitive deps (`cublas` → `cudart`; `cudnn` → `cublas`). Explicit `ctypes.CDLL` preload guarantees the DLLs are resident before CTranslate2's `LoadLibrary`.

## Resolved decisions

1. **Dep set.** `uv.lock` confirmed only `nvidia-cuda-runtime-cu12` was present at sprint start. `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` are **not** transitive — must be added directly. Default `whisper_compute_type = "float16"` (`config.py:41`) requires cuDNN. `nvidia-cuda-nvrtc-cu12` is **not** needed (CTranslate2 does not JIT).
2. **Platform markers.** All three `nvidia-*` deps gated with `; sys_platform == "win32"` so Linux/macOS users do not pay the ~600 MB wheel cost.
3. **No idempotency requirement.** Module-import scope guarantees one-time execution; idempotency hardening dropped.
4. **DEBUG logging only.** The shim runs at import time before config is read, so it cannot tell whether the user wants CUDA inference. WARNING-level noise on CPU-only Linux users is unwanted; CTranslate2's own `LoadLibrary` error will be loud enough on the actual GPU path.

## Critical files

| File | Change |
|---|---|
| `src/omniscribe/asr/whisper.py` | Add cudnn preload (after cublas); drop stale nvrtc docstring sentence; add 1-line DEBUG summary log |
| `pyproject.toml` | Add cublas + cudnn direct deps with `; sys_platform == "win32"` markers and tight upper bounds |
| `uv.lock` | Regenerate via `uv sync` |
| `tests/test_whisper_dll_shim.py` (**new**) | 4 tests, flat `tests/` layout per existing convention |
| `README.md` | One sentence under "Requirements" announcing bundled CUDA libs on Windows |

Concrete `pyproject.toml` dep block:

```toml
"nvidia-cuda-runtime-cu12>=12,<13 ; sys_platform == 'win32'",
"nvidia-cublas-cu12>=12,<13       ; sys_platform == 'win32'",
"nvidia-cudnn-cu12>=9,<10         ; sys_platform == 'win32'",
```

## Hardening checklist for `whisper.py` shim

- [x] Module-private name (`_register_nvidia_dll_dirs`) — already correct
- [x] Logs at DEBUG for the registration / preload summary
- [x] Failure path: log at DEBUG (not WARNING) when no `nvidia/` dir found.
- [x] **Preload `cudnn64_9.dll` from `nvidia/cudnn/bin/` AFTER cublas** (cuDNN links against cuBLAS). Same `contextlib.suppress(OSError)` wrap. Final order: `cudart → cublas → cudnn`.
- [x] One-line DEBUG summary at end of shim:
      `logger.debug("nvidia DLL shim: registered %d dir(s), preloaded %d DLL(s)", n_dirs, n_preloaded)`
- [x] No `# noqa: E402` proliferation — current two markers are sufficient.
- [x] Drop stale "nvrtc may not be needed at all but is harmless to preload" docstring sentence — the shim never preloads nvrtc and CTranslate2 doesn't need it.

## Tests (new file `tests/test_whisper_dll_shim.py`)

All four tests must run on Linux CI without touching real Windows APIs. Use `unittest.mock.patch("os.add_dll_directory", create=True)` — without `create=True` Linux raises `AttributeError` because `os.add_dll_directory` does not exist on POSIX.

1. `test_shim_noop_on_non_windows` — leave `sys.platform="linux"`, snapshot `len(_nvidia_dll_cookies)` before, call function, assert length unchanged. (Snapshot pattern avoids ordering coupling: the list is module-scope state populated at import time.)
2. `test_shim_handles_missing_nvidia_dir` — patch `sys.platform="win32"`, monkeypatch `sys.path=[]`, mock `os.add_dll_directory` (with `create=True`) and `ctypes.CDLL`, assert no raise, neither mock called.
3. `test_shim_preloads_when_dlls_present` — fake nvidia tree in `tmp_path` with `cuda_runtime/bin/`, `cublas/bin/`, `cudnn/bin/` subdirs containing the three DLLs. Mock `os.add_dll_directory` (with `create=True`) + `ctypes.CDLL`. Assert all three DLLs preloaded in order: `cudart64_12.dll` → `cublas64_12.dll` → `cudnn64_9.dll`.
4. `test_shim_oserror_from_add_dll_directory_does_not_break_preload` — regression guard for the existing `contextlib.suppress(OSError)` at the registration step. Mock `os.add_dll_directory` to raise `OSError("simulated")`; assert function returns normally and ctypes preload is still attempted (at least cudart).

## Reused utilities

- `logging` (already in module)
- `pathlib.Path` (already imported)
- `contextlib.suppress` (already imported)
- No new runtime dependencies beyond the three `nvidia-*` wheels

## Acceptance criteria

1. `uv run ruff format .` and `uv run ruff check .` clean.
2. `uv run pytest -q` — existing 296 tests still green + 4 new shim tests pass on Linux CI.
3. **Manual smoke (Windows RTX 4090, no system CUDA):**
   `OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 --language en -o out.json`
   produces a JSON transcript with ≥ 1 SPEECH segment, AND the log contains the line
   `nvidia DLL shim: registered N dir(s), preloaded 3 DLL(s)` (N ≥ 3 for cuda_runtime + cublas + cudnn), AND no `LoadLibrary` errors.
4. **Manual sanity (Linux):** `OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 …` produces no log line containing `nvidia DLL shim` and transcribe still works against system CUDA.
5. Close-out footer added to this doc post-merge.

## Verification commands

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest -q
OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 --language en -o out.json
```

## Out of scope

- Linux/macOS DLL handling
- A Windows GPU CI job
- Bundling `nvidia-cuda-nvrtc-cu12` (CTranslate2 doesn't JIT)
- Migrating the cookies list to a context manager (intentional: cookies must outlive import)

## PR + close-out

- Commit: `fix(asr): preload CUDA-12 cudart/cublas/cudnn DLLs on Windows`
- Open as PR off `main`, squash-merge after review
- Capture to Open Brain: which `nvidia-*` packages were required, that the `float16` default drove the cudnn dep, and whether the manual smoke confirmed the new DEBUG summary line with `preloaded=3`

## Close-out

_TBD — filled in after merge._
