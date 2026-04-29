# OmniScribe — Sprint 7.4: cuDNN sub-library preload

**Goal:** Extend the Sprint 7.2/7.3 Windows DLL preload shim to glob-load **all** cuDNN sub-libraries so onnxruntime-gpu's `CUDAExecutionProvider` (used by RapidOCR) can resolve `cudnnCreate` — closing out the residual `Invalid handle. Cannot load symbol cudnnCreate` failure surfaced after Sprint 7.3 (`ca0ab2e`) merged.

## Context

Sprint 7.2 (PR #22, `0717d8d`) and Sprint 7.3 (PR #23, `ca0ab2e`) added a Windows DLL preload shim in `src/omniscribe/asr/whisper.py` that ctypes-preloads `cudart → cublas → cudnn → cufft` so faster-whisper / CTranslate2 and onnxruntime-gpu can find CUDA-12 libraries from `nvidia-*` pip wheels.

Manual smoke after 7.3 confirmed the cufft fix landed (`cufft64_11.dll missing` error gone), but surfaced a new failure when ORT (used by RapidOCR) tries to init:

```
[INFO] Using engine_name: onnxruntime
Invalid handle. Cannot load symbol cudnnCreate
```

**Whisper still works** with the same cuDNN install (CTranslate2 succeeds at audio processing in the same run) — only ORT fails.

## Root cause

NVIDIA split cuDNN 9 into **9 DLLs**. `cudnn64_9.dll` (the only file Sprints 7.2/7.3 preload) is a thin loader stub. The actual API symbols, including `cudnnCreate`, live in sub-libraries:

```
.venv/Lib/site-packages/nvidia/cudnn/bin/
├── cudnn64_9.dll                         ← loader stub (only one we preload today)
├── cudnn_adv64_9.dll
├── cudnn_cnn64_9.dll
├── cudnn_engines_precompiled64_9.dll
├── cudnn_engines_runtime_compiled64_9.dll
├── cudnn_engines_tensor_ir64_9.dll
├── cudnn_graph64_9.dll
├── cudnn_heuristic64_9.dll
└── cudnn_ops64_9.dll                     ← contains cudnnCreate (105 MB)
```

When ORT's CUDAExecutionProvider does `GetProcAddress(cudnn_handle, "cudnnCreate")`, it returns null because `cudnnCreate` isn't in the stub.

**Why CTranslate2 works:** `ctranslate2/__init__.py:20-21` already does

```python
for library in glob.glob(os.path.join(package_dir, "*.dll")):
    ctypes.CDLL(library)
```

— it glob-preloads every DLL in its package dir. ORT does NOT do the equivalent at import.

## Tier

**T2** — `python-coder` + PO review. Single-file shim change + one test rewrite + plan doc, same envelope as Sprint 7.3. Behavior-critical (silent CPU fallback breaks OCR perf) but the diff is mechanical.

## Resolved decisions

1. **Approach: hoist-stub-then-glob.** Load `cudnn64_9.dll` first by name (the stub the sub-libraries plug into), then `glob("cudnn_*.dll")` for the underscore-prefixed sub-libraries. Loop logs a DEBUG line on per-DLL failure so a future debugger can see which sub-lib bailed (rather than silent suppression). Memory cost is acceptable — Windows commits import/reloc sections on load (tens of MB across the 9 sub-libs, dominated by `cudnn_ops64_9.dll` at ~105 MB image size). CTranslate2 has shipped a similar glob-preload pattern in production for years.
2. **Rejected: `onnxruntime.preload_dlls()`.** ORT 1.20+ ships an official `preload_dlls()` API. Architecturally cleaner but requires importing `onnxruntime` and arranging a call before any provider init — touches the OCR init code, splits the DLL-load logic across two modules. Not worth the complexity when the glob approach is one tight diff in the existing shim.
3. **Walrus-gate on key presence.** `bin_dirs.get("cudnn", Path()).is_dir()` would return True for the empty `Path()` (== cwd) and silently glob `cudnn*.dll` in cwd if cudnn isn't installed. Walrus-gate (`if cudnn_dir := bin_dirs.get("cudnn")`) avoids that.
4. **DEBUG vs WARNING for sub-lib preload failures.** Log at DEBUG, not WARNING. A downstream RapidOCR/ORT failure will be loud on the actual GPU path; WARNING here would noise CPU-only Windows users on every import.

## Critical files

| File | Change |
|---|---|
| `src/omniscribe/asr/whisper.py` | Replace single-DLL cudnn preload block with hoisted stub + `glob("cudnn_*.dll")` loop; `logger.debug` on per-DLL failure; update Step-2 leading comment; add inline expected-count comment above the DEBUG summary log |
| `tests/test_whisper_dll_shim.py` | Update `test_shim_preloads_when_dlls_present` only — add 2 representative cudnn sub-DLLs (`cudnn_ops64_9.dll`, `cudnn_graph64_9.dll`) to the fake tree, bump assertion to 6 calls in sorted-ASCII order |
| `docs/plans/sprint-7-4-cudnn-sublib-preload.md` | **New** — this sprint plan doc |

No `pyproject.toml`, no `uv.lock`, no `README.md` changes — the existing `nvidia-cudnn-cu12>=9,<10 ; sys_platform == 'win32'` dep already pulls in all 9 sub-libraries (verified via `ls .venv/Lib/site-packages/nvidia/cudnn/bin/*.dll | wc -l == 9`); the README sentence already mentions `cudnn` generically.

## Shim diff (concrete)

In `_register_nvidia_dll_dirs()`, the existing single cudnn preload block becomes:

```python
# cuDNN 9 split the API across multiple DLLs. cudnn64_9.dll is the
# loader stub; cudnnCreate and friends live in cudnn_ops64_9.dll,
# cudnn_cnn64_9.dll, etc. ORT's CUDAExecutionProvider does
# GetProcAddress("cudnnCreate") on the resolved handle and needs
# all sub-libs already resident, otherwise it falls back to CPU.
# Walrus-gate on key presence: bin_dirs.get("cudnn", Path()).is_dir()
# would return True for the empty Path (== cwd) and silently glob
# cudnn*.dll in cwd if cudnn isn't installed. Walrus avoids that.
if cudnn_dir := bin_dirs.get("cudnn"):
    # Stub first so its plugin sub-libs resolve against an already-mapped image.
    _stub = cudnn_dir / "cudnn64_9.dll"
    if _stub.exists():
        try:
            ctypes.CDLL(str(_stub))
            n_preloaded += 1
        except OSError as e:
            logger.debug("cudnn stub preload failed: %s (%s)", _stub.name, e)
    # glob with underscore prefix only — avoids double-loading the stub.
    for cudnn_dll in sorted(cudnn_dir.glob("cudnn_*.dll")):
        try:
            ctypes.CDLL(str(cudnn_dll))
            n_preloaded += 1
        except OSError as e:
            logger.debug("cudnn sub-lib preload failed: %s (%s)", cudnn_dll.name, e)
```

The Step-2 leading comment now reads:

```python
# Step 2 — preload cublas/cudnn/cufft and their transitive deps.
# Order matters: cudart must load first (cublas links against it),
# then cuDNN (stub first, then sub-libs — see block below for the why),
# then cufft (independent, kept last for documented order).
```

## DEBUG summary log

The existing `logger.debug("nvidia DLL shim: registered %d dir(s), preloaded %d DLL(s)", n_dirs, n_preloaded)` line stays. On a complete Windows install `n_preloaded` reads **12** (1 cudart + 1 cublas + 1 cudnn-stub + 8 cudnn-sublibs + 1 cufft). A defensively-worded inline comment is added above the log call:

```python
# expect 12 on a full Windows install; lower means a sub-lib failed
# (look for "preload failed" debug lines emitted by the cuDNN block).
```

so a future debugger reading `preloaded 4 DLL(s)` doesn't assume regression from 7.3, and knows where the breadcrumbs are.

## Tests

Update **only** `test_shim_preloads_when_dlls_present`. Other tests (noop, missing_nvidia_dir, oserror) remain unchanged.

**Test 4 walk-through (no change needed):** test 4 lays down only `cudart64_12.dll`, `cublas64_12.dll`, `cudnn64_9.dll`. With the new code, the walrus-gate fires (cudnn key present), the stub loads, the glob `cudnn_*.dll` returns empty (no underscore-prefixed files), so total CDLL calls = 3. The existing assertion (`mock_cdll.call_count >= 1`) still passes.

**Test 3 — new fake tree (3 representative cudnn DLLs, not all 9):**

```
tmp_path/nvidia/cuda_runtime/bin/cudart64_12.dll
tmp_path/nvidia/cublas/bin/cublas64_12.dll
tmp_path/nvidia/cudnn/bin/cudnn64_9.dll
tmp_path/nvidia/cudnn/bin/cudnn_ops64_9.dll
tmp_path/nvidia/cudnn/bin/cudnn_graph64_9.dll
tmp_path/nvidia/cufft/bin/cufft64_11.dll
```

Picking 3 representatives (stub + 2 sub-libs) instead of all 9 avoids hard-coding today's NVIDIA cuDNN 9.21 layout into the test — if NVIDIA renames `cudnn_engines_tensor_ir64_9.dll` in 9.22 the test still passes.

**Assertions:**

- `mock_add.call_count == 4` (4 nvidia subdirs registered: cuda_runtime, cublas, cudnn, cufft) — preserved from the existing test 3
- `mock_cdll.call_count == 6` (1 cudart + 1 cublas + 1 cudnn-stub + 2 cudnn-sublibs + 1 cufft)
- Ordered, sorted-ASCII (because impl uses `sorted()`):
  1. ends with `cudart64_12.dll`
  2. ends with `cublas64_12.dll`
  3. ends with `cudnn64_9.dll`           (stub, hoisted explicitly)
  4. ends with `cudnn_graph64_9.dll`     (`_g` < `_o` in ASCII)
  5. ends with `cudnn_ops64_9.dll`
  6. ends with `cufft64_11.dll`

Test count stays at **325 → 325** (no test added or removed; just test 3 broadened).

## Acceptance criteria

**Test-coverage caveat:** ACs 1-2 below assert *structural regression* (preload order, count) via mocked `ctypes.CDLL`. They CANNOT detect symbol-load bugs — the very bug 7.4 fixes (stub-only preload missing `cudnnCreate`) would pass the prior unit tests. Only **manual smoke (AC #3)** validates the user-facing behavior. Manual smoke is non-negotiable before merge.

1. `uv run ruff format .` and `uv run ruff check .` clean.
2. `uv run pytest -q` — 325 passing.
3. **Manual smoke (Windows RTX 4090, no system CUDA):**
   `OMNI_LOG_LEVEL=DEBUG omniscribe transcribe <tiktok-url> --language en --ocr -o out.json`
   produces:
   - **No** `Invalid handle. Cannot load symbol cudnnCreate` error
   - **No** `Failed to create CUDAExecutionProvider` warning
   - **No** `CUDAExecutionProvider is available, but inference part is automatically shifted to be executed under CPUExecutionProvider` warning
   - DEBUG log line `nvidia DLL shim: registered N dir(s), preloaded M DLL(s)` with `M >= 11` (12 expected on a full install; `M < 12` is acceptable only if matched 1:1 with `cudnn (stub|sub-lib) preload failed` DEBUG breadcrumb lines — investigate those before merging)
   - RapidOCR `Using engine_name: onnxruntime` followed by OCR frames running in <1 s each (vs 5–80 s on CPU)
   - Transcript `out.json` written; if the TikTok has on-screen captions they appear as `ON-SCREEN` segments
4. Close-out footer added to this doc after merge.

## Pre-execution verification

- `ls .venv/Lib/site-packages/nvidia/cudnn/bin/*.dll | wc -l` returns **9** (sanity-checks the wheel ships what we expect; if the count drifts, the assertion in test 3 needs to be reconsidered)

## Verification commands

```bash
uv run ruff format .
uv run ruff check .
uv run pytest -q
uv run pytest tests/test_whisper_dll_shim.py -v
# Manual on Windows:
$env:OMNI_LOG_LEVEL="DEBUG"; uv run omniscribe transcribe "<tiktok-url>" --language en --ocr -o out.json
```

## Out of scope

- Calling `onnxruntime.preload_dlls()` (rejected — see Resolved decisions §2)
- Adding other CUDA libs (cusolver, cusparse, curand, nccl) — only fix what surfaced
- Refactoring the shim's preload chain to be data-driven config (YAGNI)
- Tightening any version pins
- Adding a "future-proofing" test for hypothetical extra cudnn sub-DLLs (the existing test 3 already exercises the glob path with multiple sub-DLLs)
- **CUDA 13 / cuDNN 10 migration** — when NVIDIA ships the next major, future-PO must update: (a) dep specifiers in `pyproject.toml` (`>=12,<13` → `>=13,<14` for cudart/cublas/cufft; `>=9,<10` → `>=10,<11` for cudnn), (b) hardcoded DLL filenames in the shim (`cudart64_12.dll`, `cublas64_12.dll`, `cudnn64_9.dll`, `cufft64_11.dll`), (c) re-run manual smoke. Tracked here so future-PO sees the touchpoints without re-discovery.

## PR + close-out

- Branch: `feat/sprint-7-4-cudnn-sublib-preload` (off `main` at `ca0ab2e`)
- Commit: `fix(asr): glob-preload all cuDNN sub-DLLs on Windows for ORT`
- Open as PR off `main`, squash-merge after review
- Capture to Open Brain after manual smoke:
  - Confirmed `n_preloaded` count (expected 12)
  - Whether OCR per-frame time actually drops to <1 s on GPU
  - Any sub-library that emitted a `cudnn sub-lib preload failed` DEBUG line

## Close-out

_TBD — filled in after merge._
