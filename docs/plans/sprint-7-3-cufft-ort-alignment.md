# OmniScribe — Sprint 7.3: cuFFT / onnxruntime-gpu CUDA-12 alignment

**Goal:** Extend the Sprint 7.2 Windows DLL bundling pattern to **cuFFT** so onnxruntime-gpu's `CUDAExecutionProvider` (used by RapidOCR) initializes successfully on a fresh Windows install — closing out the CUDA-12 alignment work that started with `681fa03` (ORT) and `feat/sprint-7-2-cuda12-whisper-dll` (faster-whisper / CTranslate2).

## Context

Sprint 7.2 (PR #22, branch `feat/sprint-7-2-cuda12-whisper-dll`) added direct deps on `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` plus a Windows-only `ctypes`/`os.add_dll_directory` shim. Manual smoke on 2026-04-29 confirmed faster-whisper loads on CUDA + float16 with no `LoadLibrary("cublas64_12.dll")` errors.

The same smoke surfaced a related but distinct issue on the **onnxruntime-gpu** side (used by RapidOCR):

```
Error loading onnxruntime_providers_cuda.dll which depends on
"cufft64_11.dll" which is missing
[WARNING] CUDAExecutionProvider is available, but inference part is
automatically shifted to be executed under CPUExecutionProvider.
```

Effect: RapidOCR silently falls back to CPU, drastically slowing OCR frame processing (5–80s per frame vs <1s on GPU).

`cufft64_11.dll` = cuFFT major version 11, which ships in CUDA 12 (CUDA-12 cuFFT is versioned `11.x`). The `nvidia-cufft-cu12` pip package supplies it. Sprint 7.2 deliberately scoped only Whisper deps; cuFFT was not added.

This sprint adds cuFFT to the Windows DLL bundling so onnxruntime's `CUDAExecutionProvider` can initialize.

## Tier

**T2** — `python-coder` + PO review. 1–2 files, < 30 LOC, near-clone of the 7.2 pattern. Behavior-critical (silent fallback to CPU is the whole point of fixing it) but the diff is mechanical.

## Why is cuFFT needed but cuDNN/cuBLAS were enough for Whisper?

- **faster-whisper / CTranslate2** uses cuBLAS + cuDNN for matmul/conv ops; doesn't call cuFFT directly.
- **onnxruntime-gpu CUDAExecutionProvider** dynamically links cuFFT for FFT-based ops (audio/image preprocessing) — even if no FFT op is invoked at runtime, the DLL must be loadable at provider-init time, otherwise ORT abandons the CUDA provider entirely and silently falls back to CPU.

The fix is the same loader-walk-deps mitigation as 7.2: explicit pip wheel + ctypes preload.

## Resolved decisions

1. **Dep set.** Only `nvidia-cufft-cu12` is added. `cusolver`, `cusparse`, `curand`, `nccl` are **not** added — only fix what surfaced as a real error. Add follow-up sprints if/when those manifest.
2. **Platform marker.** `; sys_platform == 'win32'` so Linux/macOS users do not pay the ~100 MB wheel cost.
3. **Preload order.** cuFFT is independent of cuBLAS/cuDNN, but kept last in the preload sequence so the order matches the documented sequence: `cudart → cublas → cudnn → cufft`.
4. **Shim refactor deferred.** The shim now has 4 nearly-identical preload blocks. Migrating to a data-driven list is YAGNI for one more entry; revisit only if a 5th DLL is added.

## Critical files

| File | Change |
|---|---|
| `pyproject.toml` | Add `nvidia-cufft-cu12>=11,<12 ; sys_platform == 'win32'` next to the existing `nvidia-*` block |
| `src/omniscribe/asr/whisper.py` | Add cuFFT preload to `_register_nvidia_dll_dirs()` after the cudnn block. New order: `cudart → cublas → cudnn → cufft`. DEBUG summary now reads `preloaded=4` on a full Windows install. |
| `uv.lock` | Regenerate via `uv sync` |
| `tests/test_whisper_dll_shim.py` | Update `test_shim_preloads_when_dlls_present` — add `cufft/bin/cufft64_11.dll` to the fake tree, assert all four DLLs preloaded in order. Other 3 tests unchanged. |
| `README.md` | Tweak existing sentence: add `cufft` to the bundled-libraries list |
| `docs/plans/sprint-7-3-cufft-ort-alignment.md` | **New** — this sprint plan doc |

Concrete `pyproject.toml` addition (placed adjacent to the existing three nvidia-* lines):

```toml
"nvidia-cufft-cu12>=11,<12        ; sys_platform == 'win32'",
```

Updated README sentence:
> On Windows, CUDA 12 runtime libraries (cuda_runtime, cublas, cudnn, **cufft**) are bundled via pip — no separate CUDA toolkit install required. A system CUDA install, if present, is not used.

## Shim diff (sketch)

In `_register_nvidia_dll_dirs()` after the cudnn preload block:

```python
_preload = bin_dirs.get("cufft", Path()) / "cufft64_11.dll"
if _preload.exists():
    with contextlib.suppress(OSError):
        ctypes.CDLL(str(_preload))
        n_preloaded += 1
```

The DEBUG log message stays identical (`registered %d dir(s), preloaded %d DLL(s)`) — `n_preloaded` will read 4 instead of 3 on a fresh Windows install with all four packages.

## Tests

Update `test_shim_preloads_when_dlls_present`:
- Add `cufft/bin/cufft64_11.dll` to the fake tree
- Bump `mock_add.call_count` and `mock_cdll.call_count` from 3 → 4
- Order: `cudart64_12.dll → cublas64_12.dll → cudnn64_9.dll → cufft64_11.dll`

No new test file. Other 3 existing tests don't change. Total test count stays at 325.

## Acceptance criteria

1. `uv run ruff format .` and `uv run ruff check .` clean.
2. `uv run pytest -q` — 325 → 325 (no test count change; existing test 3 just covers more).
3. **Manual smoke (Windows RTX 4090, no system CUDA):** `OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 --language en --ocr -o out.json` produces:
   - Same Whisper success as 7.2
   - **No** `Error loading onnxruntime_providers_cuda.dll which depends on "cufft64_11.dll"` error
   - **No** `CUDAExecutionProvider is available, but inference part is automatically shifted to be executed under CPUExecutionProvider` warning
   - RapidOCR runs on `CUDAExecutionProvider` (visible from the `Using engine_name: onnxruntime` log paired with absence of the fallback warning)
   - OCR frame processing time drops from ~5–80s/frame (CPU) to <1s/frame (GPU)
   - DEBUG summary line reads `preloaded 4 DLL(s)` (cudart + cublas + cudnn + cufft)
4. **Manual sanity (Linux):** `OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 …` produces no log line containing `nvidia DLL shim` and transcribe still works against system CUDA.
5. Close-out footer added to this doc post-merge.

## Verification commands

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run pytest -q
OMNI_LOG_LEVEL=DEBUG omniscribe transcribe sample.mp4 --language en --ocr -o out.json
```

(Use `--ocr` flag to force OCR enable so the cufft path is exercised.)

## Out of scope

- Adding cuFFT to non-Windows platforms (Linux/macOS users typically have system CUDA or use ORT-CPU)
- Hunting other potentially missing CUDA DLLs (`cusolver`, `cusparse`, `curand`, `nccl`) — only fix what surfaced as a real error. Add follow-up sprints if/when those manifest.
- Refactoring the shim's preload-list to be data-driven (currently 4 hardcoded blocks). Possible future cleanup but YAGNI for now.
- A Windows GPU CI job (still relies on manual smoke).

## PR + close-out

- Commit: `fix(asr): preload CUDA-12 cuFFT DLL on Windows for ORT alignment`
- Open as PR off `main` (after PR #22 merges to avoid conflict)
- Capture to Open Brain: that cuFFT was the missing 4th DLL needed for onnxruntime-gpu `CUDAExecutionProvider` to initialize, and that the DEBUG summary now reads `preloaded=4` on a full Windows install

## Close-out

**Merged 2026-04-29** as PR [#23](https://github.com/dagonet/OmniScribe/pull/23), squash commit `ca0ab2e`. Single feature commit (`d70388f`). Plan went through 2 challenge rounds — caught the missing cuDNN preload (later 7.4 territory) and the test mock `create=True` requirement before any code was written.

**Final test count:** 325 → 325 (no test added; `test_shim_preloads_when_dlls_present` broadened from 3 → 4 expected CDLL calls in deterministic order). ruff format/check clean. `nvidia-cufft-cu12==11.4.1.4` resolved into `uv.lock` with `sys_platform == 'win32'` marker.

**Deviations from plan:** None. The shim's existing comment was lightly expanded to call out the ORT-fallback rationale alongside the new cuFFT block — consistent with the plan's intent.

**Manual smoke (Windows RTX 4090, no system CUDA):** The `cufft64_11.dll missing` error went away — Sprint 7.3's stated narrow goal achieved. **However**, the same smoke surfaced a *different* ORT failure: `Invalid handle. Cannot load symbol cudnnCreate`. Root cause: cuDNN 9 was split into 9 DLLs and Sprints 7.2/7.3 only preloaded the `cudnn64_9.dll` loader stub; `cudnnCreate` lives in the un-preloaded `cudnn_ops64_9.dll`. PR #23 merged anyway because its narrow scope (cuFFT) was correctly fixed; the cudnn sub-library issue was a separate scope.

**Follow-ups (post-merge):** cuDNN sub-library preload tracked as Sprint 7.4 (PR #24, merged same day).
