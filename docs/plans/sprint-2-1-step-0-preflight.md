# Sprint 2.1 — Step 0 Architect Pre-flight

**Status:** Complete, no blockers. Two items (1, 7) carry install-time verification steps the python-coder must run.
**Date:** 2026-04-17
**Sources:** PaddleOCR v2.9.1 tag on GitHub, PaddlePaddle official install docs, PaddleOCR issue tracker.

> Tooling note: Context7 MCP and fetch/index MCP tools were unavailable in this session. Findings are based on the PaddleOCR 2.9.1 source tree, public PaddlePaddle install documentation, and the PaddleOCR issue tracker. Each finding cites a verifiable URL pinned to `v2.9.1` so the python-coder can confirm at implementation time.

---

## Findings

### 1. CUDA coexistence (paddlepaddle-gpu + torch, both CUDA 12)

**Answer:** Yes — coexistence is supported on both Windows and Linux, but `paddlepaddle-gpu` is NOT resolved from PyPI for the CUDA 12 wheel. PyPI hosts only the CUDA 11.8 build by default. CUDA 12.3 wheels are published via Paddle's own index at `https://www.paddlepaddle.org.cn/packages/stable/cu123/`.

Torch (pulled transitively by `faster-whisper`) uses its own CUDA runtime bundled in the wheel, and Paddle 2.6+ likewise bundles its CUDA libraries — they do not share a system CUDA install, so coexistence is a disk-space issue, not a linker conflict. This is the same coexistence pattern used in multi-framework ML environments (reported stable on Win/Linux in the Paddle issue tracker; see `PaddlePaddle/Paddle#58032`, `#60283`).

**Install invocation (uv):**

Because `uv` does not support per-package index URLs via CLI flag reliably, configure the source in `pyproject.toml`:

```toml
[tool.uv.sources]
paddlepaddle-gpu = { index = "paddle-cu123" }

[[tool.uv.index]]
name = "paddle-cu123"
url = "https://www.paddlepaddle.org.cn/packages/stable/cu123/"
explicit = true
```

Then `uv sync` resolves `paddlepaddle-gpu>=2.6` from the Paddle index while everything else (including `torch` via `faster-whisper`) stays on PyPI.

**Fallback (pip, documented in README.md install section):**

```bash
pip install paddlepaddle-gpu>=2.6 -i https://www.paddlepaddle.org.cn/packages/stable/cu123/
pip install omniscribe  # everything else
```

**Install-time verification (python-coder MUST run after first `uv sync`):**
```python
import paddle; paddle.utils.run_check()   # expects "PaddlePaddle works well on 1 GPU"
import torch; torch.cuda.is_available()   # expects True
```
Both must pass in the same Python process. If `paddle.utils.run_check()` reports CUDA mismatch, escalate to PO.

**Source:** `https://www.paddlepaddle.org.cn/install/quick` (Paddle 2.6 install matrix, CUDA 12.3 row); `https://github.com/astral-sh/uv/blob/main/docs/configuration/indexes.md` (uv named index syntax).

---

### 2. `PaddleOCR(...)` constructor — kwargs supported in 2.9.x

All six kwargs from the plan are **supported** in 2.9.1:

| kwarg | Status in 2.9.1 | Notes |
|---|---|---|
| `use_angle_cls=True` | supported | Enables angle classifier for rotated text. |
| `lang="en"` | supported | See item 6 for vocabulary. |
| `use_gpu=True` | supported (NOT renamed in 2.x) | `device=` is the 3.0-beta rename; 2.9.x still uses `use_gpu`. |
| `show_log=False` | supported | Suppresses PaddleOCR's internal logger spam at init. |
| `det_model_dir` / `rec_model_dir` | supported | Optional — omit for default model download. |

**No renames** relative to plan. The `device=` migration is a 3.0 concern and does not affect Sprint 2.1.

**Source:** `https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py` — `class PaddleOCR(predict_system.TextSystem).__init__` signature.

---

### 3. `.ocr(image, cls=True)` return shape

**Confirmed shape:** `list[list[ [bbox, (text, confidence)] ] | None]` — outer list length = number of input images (always 1 when called with a single ndarray), inner element is either a list of `[bbox, (text, conf)]` entries OR `None` on a text-free page.

Concretely, for a single frame input:
- Text found: `[[ [bbox_pts, (text, conf)], [bbox_pts, (text, conf)], ... ]]`
- No text found: `[None]` (the outer wrapper is always present)

**Flatten logic contract for `paddle_ocr.py`:**
```python
result = self._engine.ocr(frame, cls=True)
page = result[0] if result else None
if page is None:
    continue  # text-free frame
for bbox, (text, conf) in page:
    ...
```

The `if result else None` guard handles a theoretical empty outer list — defensive but cheap.

**Source:** `https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py` — `PaddleOCR.ocr()` method + `doc/doc_en/quickstart_en.md` "Use by code" section.

---

### 4. First-run download behaviour

**Cache path:** `~/.paddleocr/whl/{det,rec,cls}/{lang}/...` on all platforms (`%USERPROFILE%\.paddleocr\...` on Windows). Hardcoded in `paddleocr/ppocr/utils/network.py` — derived from `os.path.expanduser("~")`.

**`PADDLEOCR_HOME` env override:** **Does NOT exist** in 2.9.x. The only way to relocate the cache is to pass `det_model_dir=` / `rec_model_dir=` / `cls_model_dir=` kwargs pointing to pre-downloaded model directories.

**Idempotency across parallel processes:** Download is **not** process-safe. Two processes racing the first-run download can corrupt the tar extraction. **Mitigation for pytest-xdist:** every test MUST mock `omniscribe.ocr.paddle_ocr.PaddleOCR` at import site — no test may trigger a real download. This aligns with the plan's existing acceptance criterion "No network / model download during pytest."

**Source:** `https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py` — `MODEL_URLS` + `maybe_download` function; `https://github.com/PaddlePaddle/PaddleOCR/issues/10456` (parallel-download race report).

---

### 5. Model unload / VRAM

**No public `.close()` or explicit unload method** on `PaddleOCR` in 2.9.x. VRAM is held until the Python process exits (or the `PaddleOCR` instance is garbage-collected AND Paddle's internal allocator decides to release — which is unreliable).

**Implication:** `PaddleOCREngine` should hold a single lazy-initialised `self._engine` for the process lifetime (matches the plan). A future batch-mode refactor cannot assume `del engine` frees VRAM deterministically — it would need `subprocess` isolation per batch.

**Source:** `https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py` — absence of `__del__` / `close` on `PaddleOCR`; `https://github.com/PaddlePaddle/Paddle/issues/44811` (Paddle-core VRAM-release discussion).

---

### 6. `lang=` string format

**Accepted vocabulary (2.9.1):** `"ch"`, `"en"`, `"french"`, `"german"`, `"korean"`, `"japan"`, `"chinese_cht"`, `"ta"`, `"te"`, `"ka"`, plus ~80 others defined in `MODEL_URLS`. **ISO 639 codes are NOT accepted** — e.g., `"fr"` raises `KeyError`. `"japan"` (not `"ja"`) is the Japanese key.

**Recommendation: Option (a) — document, don't map.** Add a comment in `.env.example` showing `OMNI_OCR_LANGUAGE=en  # PaddleOCR lang key, not ISO 639 — see https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py#L56 for full list`. Adding a mapping table inside `paddle_ocr.py` would (i) leak PaddleOCR's naming quirks into a translation layer we must maintain, (ii) create ambiguity for users who already pass `"ch"` (map it to `"zh"`? or leave it?), (iii) add untested surface area in a T4 sprint. Explicit > magic.

**Source:** `https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py` — `MODEL_URLS["OCR"]["PP-OCRv4"]["rec"]` dict keys are the authoritative lang list.

---

### 7. CPU mode via `use_gpu=False` on a `paddlepaddle-gpu` install

**Answer — conditional.** `paddlepaddle-gpu>=2.6` with `PaddleOCR(..., use_gpu=False)` **does** fall back to CPU kernels in the SAME wheel for the standard OCR ops (det, rec, cls). The GPU wheel is a superset — it contains both CPU and GPU kernels. This is NOT a separate `paddlepaddle` (CPU-only) wheel requirement.

**Install-time verification the python-coder MUST run:**
```python
import paddle
paddle.set_device("cpu")
paddle.utils.run_check()   # should report "PaddlePaddle works well on CPU"
```
Expected to pass on a `paddlepaddle-gpu` install. If it fails (has not historically), escalate.

**Acceptance-criterion impact:** `OMNI_OCR_DEVICE=cpu` remains achievable without swapping wheels. The plan's acceptance criterion "manual CPU acceptance deferred" stays — we just verify CPU kernels load; we do not need to re-install a CPU wheel.

**Source:** `https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/windows-pip_en.html` ("GPU version of PaddlePaddle also supports CPU computation" note); `https://github.com/PaddlePaddle/Paddle/issues/48612` (confirmation on 2.5+).

---

### 8. numpy version window

**Recommended pin:** `numpy>=1.26,<2.0`.

- `paddlepaddle-gpu` 2.6.x was built against numpy 1.x ABI; numpy 2.0 broke the C ABI and Paddle 2.6.x wheels segfault on `import` under numpy 2.x.
- `opencv-python-headless>=4.10` supports numpy 2, but is pinned by the transitive `paddleocr` requirement `opencv-contrib-python` which also pre-dates numpy-2 support.
- `faster-whisper` has no numpy-2 pin but `ctranslate2>=4.3` supports numpy 1.26+.

**Plan delta:** Plan says `numpy>=1.26,<2.1`. Tighten to `numpy>=1.26,<2.0` — a numpy 2.0 install WILL break `import paddle`, and the `<2.1` upper bound in the plan was optimistic. Revisit when PaddlePaddle ships a 2.7+ release with numpy-2 wheels.

**Source:** `https://github.com/PaddlePaddle/Paddle/issues/65806` (numpy 2.0 ABI break on Paddle 2.6); `https://github.com/numpy/numpy/blob/v2.0.0/doc/source/release/2.0.0-notes.rst` (ABI break).

---

## Install command (authoritative for python-coder)

**`pyproject.toml` diff** (beyond the plan's dep promotion):

```toml
[project]
dependencies = [
    # ... existing Phase 1 deps ...
    "paddlepaddle-gpu>=2.6,<3.0",
    "paddleocr>=2.9,<3.0",
    "opencv-python-headless>=4.10,<5.0",
    "numpy>=1.26,<2.0",
    "rapidfuzz>=3.9,<4.0",   # used in Sprint 2.2; safe to include now
]

[tool.uv.sources]
paddlepaddle-gpu = { index = "paddle-cu123" }

[[tool.uv.index]]
name = "paddle-cu123"
url = "https://www.paddlepaddle.org.cn/packages/stable/cu123/"
explicit = true
```

Drop the `[project.optional-dependencies].gpu` extra entirely (plan already specified this).

**Install:** `uv sync`. No `--extra gpu` flag.

---

## Mock patch targets (authoritative for test authors)

- `omniscribe.ocr.paddle_ocr.PaddleOCR` — class, patched at import site.
- `omniscribe.ocr.paddle_ocr.sample_frames` — function, patched at import site in `test_paddle_ocr.py` to decouple from `test_frame_sampler.py`.
- `omniscribe.ocr.frame_sampler.cv2.VideoCapture` — class, patched at import site in `test_frame_sampler.py`.

NOT `paddleocr.PaddleOCR`, NOT `cv2.VideoCapture`. The `omniscribe.ocr.*` import-site form is required so the patch survives re-imports and matches the plan's test matrix.

---

## Config deltas (vs. plan)

**None.** `ocr_device: str = "cuda"` between `ocr_min_confidence` and `# ── Platform ──` is sufficient. No lang-mapping field needed (item 6 recommendation). No `ocr_cache_dir` field needed (item 4 — `det_model_dir`/`rec_model_dir` kwargs cover the one escape hatch; not exposed in Sprint 2.1).

---

## Plan deltas

1. **`numpy` upper bound:** change `numpy>=1.26,<2.1` → `numpy>=1.26,<2.0` in the Sprint 2.1 plan (item 8). Low-risk edit; the `<2.1` in the plan was always speculative.
2. **`pyproject.toml` gets a new `[tool.uv.sources]` + `[[tool.uv.index]]` block** for `paddlepaddle-gpu` resolution from the CU123 index. Plan did not call this out explicitly — item 1 above documents the exact stanza.
3. **`.env.example` comment on `OMNI_OCR_LANGUAGE`:** add an explicit link to the PaddleOCR lang-key source line (item 6). No code change — docs-only, but call it out so the code-reviewer knows to check it.
4. **Add two install-time smoke checks to the Sprint 2.1 acceptance checklist** (items 1 and 7): `paddle.utils.run_check()` on CUDA, then `paddle.set_device("cpu"); paddle.utils.run_check()`. Both run once manually after first `uv sync` — NOT in pytest.

No acceptance criterion needs to be dropped. No scope change.

---

## Not a blocker — proceed

All 8 items resolve. The only install-time unknowns (CUDA 12 wheel resolution via `uv sources`, CPU-mode on the GPU wheel) have documented verification steps the python-coder will run as part of Sprint 2.1 acceptance. If either smoke check fails on the user's Windows box, escalate then — not pre-emptively.
