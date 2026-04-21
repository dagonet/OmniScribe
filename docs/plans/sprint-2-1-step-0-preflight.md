> **[SUPERSEDED 2026-04-17]**
>
> This pre-flight note targeted PaddleOCR + paddlepaddle-gpu. It is no longer relevant.
>
> **Why superseded:** The `paddlepaddle-gpu` CU123 wheel index has zero stable Windows wheels (only pre-release `3.0.0rc1`) and no 2.x wheels at all — PaddleOCR is effectively a Linux-only dependency in its current packaging. Sprint 2.1 was re-planned around **RapidOCR** (ONNX Runtime-based, pure PyPI, Windows + Linux wheels).
>
> The canonical plan is now `docs/plans/sprint-2-1-ocr-foundation.md` (re-issued). See git history of this file on `feat/sprint-2-1-ocr-foundation` (commits `2d8fc8b` and prior) for the original content retained for audit.
>
> **Three empirical probes preserved for the record:**
>
> 1. `uv pip compile --python-platform windows --index-url https://www.paddlepaddle.org.cn/packages/stable/cu123/ paddlepaddle-gpu` → "no wheels with matching platform tag `win_amd64`".
> 2. `paddleocr` 3.4.1 on PyPI pulls `paddlex[ocr-core]>=3.4.0,<3.5.0` instead of being self-contained — major architectural shift vs the 2.9.x line we originally planned around.
> 3. `paddlepaddle-gpu` on PyPI tops at `2.6.2` (CUDA 11.8). 3.x is Paddle-index-only, Linux-only stable.
>
> No follow-up action; no blocker to un-block. Sprint 2.1 resumes under the RapidOCR plan.

## Close-out

This pre-flight doc is **superseded** — not shipped as a feature. The underlying pivot (PaddleOCR → RapidOCR) shipped as part of Sprint 2.1 via:

| Context | SHA | Summary |
|---|---|---|
| Pivot | `9116bfc` | `docs(phase-2): pivot OCR engine from PaddleOCR to RapidOCR` — docs-only commit capturing the decision. |
| Sprint 2.1 | `441e70d`..`e9b18c1` | Deps swapped to RapidOCR + `onnxruntime-gpu`; paddlepaddle-gpu / `[gpu]` extra dropped. |

No open follow-ups. Retained only as audit trail for the three empirical probes that motivated the pivot.
