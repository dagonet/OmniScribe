# OmniScribe — Sprint 2.1: Deps + frame sampler + OCR engine + CLI wiring (RapidOCR)

**Tier:** T3
**Team:** python-coder, code-reviewer, tester
**Supersedes:** the PaddleOCR-based Sprint 2.1 plan (and its two Step 0 pre-flight attempts). PaddleOCR was abandoned after empirical wheel probing confirmed no stable Windows `paddlepaddle-gpu` exists on the CU123 index. See `docs/plans/sprint-2-1-step-0-preflight.md` (SUPERSEDED banner + audit-trail probes).

Parent plan: `docs/plans/phase-2-ocr.md`. Builds on Phase 1 MVP at `91bfaa7`.

## Goal

`omniscribe transcribe <src> --ocr` emits a JSON transcript with interleaved `SPEECH` + `ON-SCREEN` segments from RapidOCR inference on sampled frames. GPU inference via `onnxruntime-gpu` (CUDA 12); CPU fallback in the same wheel.

## Why RapidOCR

- **Pure PyPI install** — no special index, no `[tool.uv.sources]`, no wheel-matrix archaeology. `rapidocr` + `onnxruntime-gpu` resolve clean on Windows **and** Linux for Python 3.11 and 3.12.
- **Same underlying models as PaddleOCR** — rapidocr ships PP-OCRv4 / PP-OCRv5 weights in ONNX form. Recognition quality parity, without the paddle dep.
- **No CUDA coexistence drama** — `onnxruntime-gpu` and faster-whisper's `torch` both ship their own CUDA libraries bundled per-wheel. Coexistence is a disk-space question, not a linker conflict.
- **Tier drops T4 → T3** — no architect pre-flight is load-bearing anymore. Library is stable, documented, and on PyPI.

## Tier justification

~180 prod LOC. The original T4 call was driven by CUDA coexistence risk with paddlepaddle-gpu; that risk is gone. Remaining complexity (new `ocr/` subpackage, first external ML engine beyond faster-whisper) fits cleanly within T3. No architect Step 0 required.

## Design decisions locked

- **`rapidocr>=3.8,<4.0`** — ONNX-based. Import: `from rapidocr import RapidOCR`. Call: `engine(img_content)` where `img_content: str | np.ndarray | bytes | Path`.
- **`onnxruntime-gpu>=1.18,<2.0`** as main dep — superset of CPU kernels. `ocr_device="cpu"` disables the CUDA provider via `params={"EngineConfig.onnxruntime.use_cuda": False}`. No separate CPU-only wheel needed.
- **`opencv-python` (non-headless)** pulled transitively by rapidocr — we do NOT add `opencv-python-headless`. Dual-install conflict.
- **`numpy>=1.26,<3.0`** — matches rapidocr's own pin; numpy 2.x is allowed (relaxed from the Paddle-era `<2.0`).
- **`requires-python = ">=3.11,<3.13"`** — `onnxruntime-gpu` 1.24.x Windows wheels cover cp311 + cp312 only. Revisit when ORT ships 3.13 Windows wheels.
- **GPU selection** via `params={"EngineConfig.onnxruntime.use_cuda": True, "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0}`. No runtime CUDA→CPU fallback — init failure raises `OmniScribeError`.
- **Language mapping** — RapidOCR uses its own `LangRec` enum (`LangRec.EN`, `LangRec.CH`, etc.), not ISO 639. `config.ocr_language` is documented as a `LangRec` value string (e.g., `"en"`, `"ch"`, `"japan"`, `"korean"`) and passed through verbatim to `params={"Rec.lang_type": config.ocr_language, "Det.lang_type": config.ocr_language}`. If a user passes `"fr"` it will fail at engine init with a clear error — that's acceptable.
- **Mock patch targets** at import site: `omniscribe.ocr.rapid_ocr.RapidOCR`, `omniscribe.ocr.rapid_ocr.sample_frames`, `omniscribe.ocr.frame_sampler.cv2.VideoCapture`.
- **pytest `--strict-markers`** remains authoritative.

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `pyproject.toml` (edit) | Remove the `[gpu]` extra entirely (was `paddlepaddle-gpu`). Add to `[project] dependencies`: `rapidocr>=3.8,<4.0`, `onnxruntime-gpu>=1.18,<2.0`, `numpy>=1.26,<3.0`, `rapidfuzz>=3.9,<4.0` (used in Sprint 2.2; safe to include now). Tighten `requires-python = ">=3.11,<3.13"` with comment noting onnxruntime-gpu Windows wheel coverage. **No `[tool.uv.sources]` block** — everything resolves from PyPI. | ~10 diff |
| `.env.example` (edit) | Add `OMNI_OCR_DEVICE=cuda`. Update `OMNI_OCR_LANGUAGE` comment: `# RapidOCR LangRec value (e.g., "en", "ch", "japan", "korean") — not ISO 639. See https://github.com/RapidAI/RapidOCR/blob/v3.8.1/python/rapidocr/utils/typings.py for the full enum.` | +2 |
| `src/omniscribe/config.py` (edit) | Add `ocr_device: str = "cuda"` field between `ocr_min_confidence` and the `# ── Platform ──` section header. No validator. | +2 |
| `src/omniscribe/ocr/__init__.py` | `"""OCR (Optical Character Recognition) subpackage."""` — docstring only, mirrors `asr/__init__.py`. | 1 |
| `src/omniscribe/ocr/frame_sampler.py` | `sample_frames(video_path: Path, fps: float) -> Iterator[tuple[float, np.ndarray]]`. Uses `cv2.VideoCapture(str(video_path.resolve()))` — explicit `str` cast (cv2 on Windows + UNC is flaky with `Path`). Stride `max(1, round(native_fps / fps))`, yields `(timestamp_seconds, bgr_frame)` at each stride. `(False, _)` from `cap.read()` is normal EOF — exit loop, don't raise. Raises `OmniScribeError` only if `cap.isOpened()` false. `try/finally cap.release()`. | ~55 |
| `src/omniscribe/ocr/rapid_ocr.py` | `RapidOCREngine(config: OmniScribeConfig)` class mirroring `WhisperTranscriber`: lazy first-call init of `RapidOCR(params={...})` with the params dict built from `config.ocr_device`, `config.ocr_language`. INFO log before init: `"Loading RapidOCR on %s — first run may download ~15 MB of ONNX models"`. Method `extract(video_path: Path) -> list[TranscriptSegment]`: drives `sample_frames`, calls `result = self._engine(frame)`, reads `result.boxes` / `result.txts` / `result.scores` (parallel-indexed). Filters by `score >= config.ocr_min_confidence`. Empty-text case: `result.boxes` is `(0, 4, 2)` shape — naturally skipped by the zip. Yields `TranscriptSegment(start=t, end=t, text=txt, source="ON-SCREEN", confidence=score, language=config.ocr_language)`. | ~90 |
| `src/omniscribe/output.py` (edit) | Add `merge_channels(speech: list[TranscriptSegment], ocr: list[TranscriptSegment]) -> list[TranscriptSegment]` = `sorted(speech + ocr, key=lambda s: s.start)` — Python stable sort + (speech-first-append) keeps SPEECH before OCR on equal-start ties deterministically. No source-enum tiebreaker. | +8 |
| `src/omniscribe/cli.py` (edit) | Add `--ocr/--no-ocr` as `Optional[bool] = typer.Option(None, "--ocr/--no-ocr")` + `--ocr-language` as `Optional[str] = typer.Option(None)`. Runtime merge: `ocr_active = flag if flag is not None else config.ocr_enabled`; if `ocr_language` override present, `config = config.model_copy(update={"ocr_language": ocr_language})`. When `ocr_active`: after ASR, call `RapidOCREngine(config).extract(video_path)` → `merge_channels(speech_segments, ocr_segments)`. Log: `"OCR: N segments from M frames"`. | +20 |
| `tests/test_frame_sampler.py` | Mock `omniscribe.ocr.frame_sampler.cv2.VideoCapture` at import site. (a) 30 fps / 90 frames / fps=1.0 → yields `t=0,1,2`; (b) `isOpened()==False` → `OmniScribeError`; (c) `release()` called on iterator exception; (d) fps=0.5 → stride 60; (e) fps > native → stride 1; (f) `VideoCapture` receives `str(path)`, not `Path`. | ~75 |
| `tests/test_rapid_ocr.py` | Patch `omniscribe.ocr.rapid_ocr.RapidOCR` + `omniscribe.ocr.rapid_ocr.sample_frames`. (a) lazy init — `extract()` triggers `RapidOCR(...)` once across multiple calls; (b) init params from config: `ocr_device="cuda"` → `params["EngineConfig.onnxruntime.use_cuda"] == True`; (c) `ocr_device="cpu"` → `False`; (d) confidence filter drops below-threshold text; (e) no-text result (empty `boxes`, `txts`, `scores`) → zero segments, no crash; (f) segment `start == end == frame timestamp`, `source == "ON-SCREEN"`, `language == config.ocr_language`; (g) INFO log emitted before first init only. Use a `SimpleNamespace` or `MagicMock` to fake `RapidOCROutput` with `.boxes` (np.ndarray), `.txts` (tuple[str]), `.scores` (tuple[float]). | ~100 |
| `tests/test_output.py` (extend) | 3 `merge_channels` tests: (a) empty speech + non-empty OCR; (b) equal-start ties → SPEECH first; (c) interleaved order by `start` preserved. | ~40 |
| `tests/test_cli.py` (extend) | (a) `--ocr` + `--language` + `--ocr-language` → interleaved Transcript, whisper gets `--language`, rapid engine receives `config.ocr_language` override; (b) `--no-ocr` → `RapidOCREngine` never instantiated (mock-verified); (c) `ocr_enabled=false` env + no flag → same as `--no-ocr`; (d) `OMNI_OCR_ENABLED=false` + `--ocr` → CLI wins; (e) zero OCR + zero speech → zero-segment Transcript. | ~80 |

## Acceptance criteria (Sprint 2.1 only)

- [ ] `uv sync` installs `rapidocr>=3.8` + `onnxruntime-gpu>=1.18` + pinned `numpy` cleanly on Win + Linux. No `[tool.uv.sources]`.
- [ ] Install-time GPU smoke check: `python -c "import onnxruntime as ort; print('providers:', ort.get_available_providers())"` → list includes `'CUDAExecutionProvider'` on the RTX 4090 dev box.
- [ ] Install-time CPU fallback smoke check: same command still succeeds if `CUDA_VISIBLE_DEVICES=""` set.
- [ ] `omniscribe transcribe <mp4> --ocr` on a local MP4 with overlay text → JSON with ≥1 `ON-SCREEN` segment alongside `SPEECH`, sorted by `start` (manual GPU accept).
- [ ] `omniscribe transcribe <mp4> --no-ocr` → identical JSON to Phase 1 (`RapidOCREngine` never instantiated — mock-verified).
- [ ] `OMNI_OCR_ENABLED=false` without flag ≡ `--no-ocr`; explicit `--ocr` overrides env.
- [ ] First `RapidOCREngine.extract()` logs INFO before ~15 MB model download.
- [ ] `OMNI_OCR_DEVICE=cpu` → `use_cuda=False` flows (mock-verified); manual CPU acceptance on the same Windows box.
- [ ] All unit tests green on CPU-only CI (every `RapidOCR` + `cv2.VideoCapture` call mocked — no real model download during `pytest`).
- [ ] `ruff format --check .` + `ruff check .` pass.

## Out of scope

Preprocessor + dedup (Sprint 2.2). Scene-change / ROI (Phase 3). Platform filters (Phase 3). ASR↔OCR merge (Phase 4). SRT/VTT/MD + `--format` (Phase 5). Runtime CUDA→CPU fallback.

## Close-out

Sprint 2.1 is **complete**. Shipped via one squash-merged PR against `main`:

| Sprint | PR | SHA | Summary |
|---|---|---|---|
| 2.1 | — | `e9b18c1` | RapidOCR engine (ONNX-based PP-OCRv4/v5, CUDA + CPU fallback via onnxruntime-gpu); frame sampler at configurable `ocr_sample_fps`; lazy-init `RapidOCREngine` wrapper; `--ocr` / `--ocr-language` CLI flags and env integration; `merge_channels` stable-sort for SPEECH↔OCR interleaving. 62 tests passing; manual GPU acceptance on RTX 4090. |

Net test delta at ship: 62 tests at `e9b18c1` (Phase 1's 38 + Sprint 2.1's 24).

Follow-ups explicitly **deferred** out of Sprint 2.1 and not yet scheduled:

- Frame preprocessor with grayscale + CLAHE (Sprint 2.2).
- Cross-frame OCR deduplicator via rapidfuzz (Sprint 2.2).
- Scene-change detection (Phase 2.5).
- ROI-aware on-screen filtering (Phase 3).
- ASR↔OCR merge engine with `source="BOTH"` tag (Phase 4).
- SRT/VTT/MD output formatters (Phase 4).
