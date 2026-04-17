# OmniScribe — Sprint 2.1: Deps + frame sampler + OCR engine + CLI wiring

**Tier:** T4
**Team:** architect, python-coder, code-reviewer, tester

Parent plan: `docs/plans/phase-2-ocr.md`. Builds on Phase 1 MVP at `91bfaa7`.

## Goal

`omniscribe transcribe <src> --ocr` emits a JSON transcript with interleaved `SPEECH` + `ON-SCREEN` segments from PaddleOCR GPU inference on sampled frames.

## Tier justification

~180 prod LOC — under the LOC-based T4 threshold. **Architectural criteria** drive the T4 call: new `ocr/` subpackage, first external ML engine beyond faster-whisper, and CUDA coexistence risk between `paddlepaddle-gpu` and `torch`. Architect Step 0 is load-bearing.

## Step 0 — Architect pre-flight (blocking)

Architect runs `mcp__plugin_context7_context7__query-docs` on `paddleocr` and `paddlepaddle-gpu` (fallback: paddleocr 2.9+ README at pinned GitHub version) to confirm:

1. **CUDA coexistence.** `paddlepaddle-gpu>=2.6` on CUDA 12 alongside faster-whisper's `torch` (CUDA 12). Confirm a CUDA 12 wheel index exists and pip install resolves cleanly on Windows + Linux. Architect proposes the exact wheel URL / `--index-url` flag if needed.
2. **`PaddleOCR(...)` current constructor** — which of `use_angle_cls`, `lang`, `use_gpu`, `show_log`, `rec_model_dir`, `det_model_dir` are still supported in 2.9+.
3. **`.ocr(image, cls=True)` return shape** — confirm shape + whether a `None` page is possible on a text-free frame.
4. **First-run download behaviour.** Det/rec/cls models (~20 MB each) cache to `~/.paddleocr`. Confirm `PADDLEOCR_HOME` override + idempotency across processes.
5. **Model unload / VRAM** — whether `PaddleOCR` exposes `.close()` / `.del` or VRAM only frees on process exit.
6. **`lang=` string format** — confirm accepted values (e.g., `"en"`, `"french"`, `"german"`, `"japan"`). If ISO 639 (`"fr"`) is rejected, architect proposes either (a) document `ocr_language` as a PaddleOCR lang string in `.env.example`, or (b) add a mapping table in `paddle_ocr.py`. PO decides.
7. **CPU mode via `use_gpu=False`.** Does `paddlepaddle-gpu>=2.6` with `use_gpu=False` run on CPU, or does recent Paddle require separate `paddlepaddle` (CPU-only) wheel?
8. **`numpy` version window.** Paddle + cv2 + paddlepaddle-gpu compatible range. Architect proposes the pin.

**Architect deliverable:** one-page note with findings + exact install command + mock patch target + any config additions. Dev spawns begin only after this lands.

**Escalation rule:** if Step 0 finds a blocker (no CUDA 12 paddle wheel; paddlepaddle-gpu + torch cannot coexist on Windows), architect posts a BLOCKER note and PO re-enters plan mode. **No silent workarounds.**

## Deliverables

| Path | Purpose | ~LOC |
|---|---|---|
| `pyproject.toml` (edit) | Promote `paddlepaddle-gpu>=2.6,<3.0` from `[gpu]` extra → main deps. Drop the now-empty `[gpu]` extra. Add `numpy>=1.26,<2.0` to main deps (numpy 2.0 breaks Paddle 2.6 ABI — Step 0 item 8). Add `[tool.uv.sources] paddlepaddle-gpu = { index = "paddle-cu123" }` + `[[tool.uv.index]] name = "paddle-cu123"` pointing at `https://www.paddlepaddle.org.cn/packages/stable/cu123/` with `explicit = true` (Step 0 item 1 — CUDA 12 wheel not on PyPI). `opencv-python-headless>=4.10,<5.0`, `paddleocr>=2.9,<3.0`, `rapidfuzz>=3.9,<4.0` stay in main — no `[ocr]` extra split (paddleocr pulls non-headless opencv-python transitively; dual-install conflict). | ~15 diff |
| `.env.example` (edit) | Add `OMNI_OCR_DEVICE=cuda`. Update `OMNI_OCR_LANGUAGE` comment: `# PaddleOCR lang key, not ISO 639 — see https://github.com/PaddlePaddle/PaddleOCR/blob/v2.9.1/paddleocr.py for full list`. | +2 |
| `src/omniscribe/config.py` (edit) | Add `ocr_device: str = "cuda"` between `ocr_min_confidence` and `# ── Platform ──`. | +2 |
| `src/omniscribe/ocr/__init__.py` | `"""OCR (Optical Character Recognition) subpackage."""` — docstring only, mirrors `asr/__init__.py`. | 1 |
| `src/omniscribe/ocr/frame_sampler.py` | `sample_frames(video_path: Path, fps: float) -> Iterator[tuple[float, np.ndarray]]`. Uses `cv2.VideoCapture(str(video_path.resolve()))` (str cast + UNC normalisation). Stride = `max(1, round(native_fps / fps))`, yields `(timestamp, bgr_frame)`. Terminates cleanly on `cap.read()` returning `(False, _)`. Raises `OmniScribeError` only if `cap.isOpened()` false. `try/finally` `cap.release()`. | ~55 |
| `src/omniscribe/ocr/paddle_ocr.py` | `PaddleOCREngine(config)` class mirroring `WhisperTranscriber`. Lazy first-call init of `PaddleOCR(use_angle_cls=True, lang=config.ocr_language, use_gpu=(config.ocr_device == "cuda"), show_log=False)`. INFO log before init ("Loading PaddleOCR %s on %s — first run may download ~60 MB"). `extract(video_path) -> list[TranscriptSegment]`: drives `sample_frames`, calls `self._engine.ocr(frame, cls=True)`, flattens page wrapper, filters by `confidence >= config.ocr_min_confidence`, yields `TranscriptSegment(start=t, end=t, text=text, source="ON-SCREEN", confidence=conf, language=config.ocr_language)`. Handles empty-page `None`. | ~90 |
| `src/omniscribe/output.py` (edit) | Add `merge_channels(speech, ocr) -> list[TranscriptSegment]` = `sorted(speech + ocr, key=lambda s: s.start)` — Python stable sort + (speech-first-append) keeps SPEECH before OCR on equal-start ties deterministically. No source-enum tiebreaker. | +8 |
| `src/omniscribe/cli.py` (edit) | Add `--ocr/--no-ocr` as `Optional[bool] = typer.Option(None, "--ocr/--no-ocr")` + `--ocr-language` as `Optional[str] = typer.Option(None)`. Runtime merge: `ocr_active = flag if flag is not None else config.ocr_enabled`; if `ocr_language` override present, `config.model_copy(update={"ocr_language": ocr_language})`. When `ocr_active`: after ASR, call `PaddleOCREngine(config).extract(video_path)` → `merge_channels(speech_segments, ocr_segments)`. Log: "OCR: N segments from M frames". | +20 |
| `tests/test_frame_sampler.py` | Mock `omniscribe.ocr.frame_sampler.cv2.VideoCapture` at import site. (a) 30 fps / 90 frames / fps=1.0 → yields t=0,1,2; (b) `isOpened()==False` → `OmniScribeError`; (c) `release()` on iterator exception; (d) fps=0.5 → stride 60; (e) fps > native → stride 1; (f) `VideoCapture` gets `str(path)`, not Path. | ~75 |
| `tests/test_paddle_ocr.py` | Patch `omniscribe.ocr.paddle_ocr.PaddleOCR` + `omniscribe.ocr.paddle_ocr.sample_frames`. (a) lazy init — `extract()` triggers `PaddleOCR(...)` once; (b) kwargs from config (`lang`, `use_gpu=True` on `ocr_device="cuda"`); (c) `ocr_device="cpu"` → `use_gpu=False`; (d) confidence filter; (e) empty-page `None` → zero segments; (f) `start == end == timestamp`, `source == "ON-SCREEN"`; (g) INFO log emitted once before first init. | ~100 |
| `tests/test_output.py` (extend) | 3 `merge_channels` tests: (a) empty speech + non-empty OCR; (b) equal-start ties → SPEECH first; (c) interleaved order by `start` preserved. | ~40 |
| `tests/test_cli.py` (extend) | (a) `--ocr` + `--language` + `--ocr-language` → interleaved Transcript; (b) `--no-ocr` → `PaddleOCREngine` never instantiated; (c) `ocr_enabled=false` env + no flag → same as `--no-ocr`; (d) `OMNI_OCR_ENABLED=false` + `--ocr` → CLI wins; (e) zero OCR + zero speech → zero-segment Transcript. | ~80 |

## Design decisions locked

- **GPU-by-default PaddleOCR** with `ocr_device` config field. Wheel index URL (if needed) goes in `pyproject.toml`'s `[tool.uv]` or `README.md`, not source.
- **`opencv-python-headless` stays in main** — `paddleocr` pulls non-headless `opencv-python` transitively; `[ocr]` extra would create a known install conflict.
- **Fixed-interval sampling only** in 2.1. Scene-change deferred.
- **Interleaving strategy:** plain `sorted(..., key=lambda s: s.start)`. Stable sort + append-order determinism.
- **`--ocr/--no-ocr` resolution:** Typer flag is `Optional[bool] = None`; runtime merges with config. Explicit CLI flag wins.
- **`numpy>=1.26,<2.0` pinned explicitly** (Step 0 item 8 — numpy 2.0 ABI break against Paddle 2.6 wheels).
- **No runtime CUDA fallback.** `ocr_device="cuda"` init failure → `OmniScribeError`. User flips env or disables OCR.
- **Mock patch targets at import site** — `omniscribe.ocr.paddle_ocr.PaddleOCR`, not `paddleocr.PaddleOCR`.
- **pytest `--strict-markers`** remains authoritative.

## Acceptance criteria (Sprint 2.1 only)

- [ ] `uv sync` installs `paddlepaddle-gpu>=2.6` from the `paddle-cu123` index + `opencv-python-headless` + `numpy>=1.26,<2.0` cleanly.
- [ ] Install-time smoke check (CUDA): `python -c "import paddle; paddle.utils.run_check()"` reports "PaddlePaddle works well on 1 GPU"; `python -c "import torch; print(torch.cuda.is_available())"` prints `True` in the same venv.
- [ ] Install-time smoke check (CPU fallback on GPU wheel): `python -c "import paddle; paddle.set_device('cpu'); paddle.utils.run_check()"` reports "PaddlePaddle works well on CPU".
- [ ] `omniscribe transcribe <mp4> --ocr` → JSON with ≥1 `ON-SCREEN` segment alongside `SPEECH`, sorted by `start` (manual acceptance on RTX 4090).
- [ ] `omniscribe transcribe <mp4> --no-ocr` → identical JSON to Phase 1 (`PaddleOCREngine` never instantiated, verifiable via mock).
- [ ] `OMNI_OCR_ENABLED=false` without CLI flag ≡ `--no-ocr`; explicit `--ocr` overrides env.
- [ ] First `PaddleOCREngine.extract()` logs INFO before ~60 MB model download.
- [ ] `OMNI_OCR_DEVICE=cpu` → `use_gpu=False` kwarg flows (mock-verified); manual CPU acceptance deferred until architect Step 0 item 7 resolves.
- [ ] All unit tests green on CPU-only CI (every CUDA / PaddleOCR call mocked).
- [ ] `ruff format --check .` + `ruff check .` pass.
- [ ] No network / model download during `pytest`.

## Out of scope

Preprocessor + dedup (Sprint 2.2). Scene-change / ROI (Phase 3). Platform filters (Phase 3). ASR↔OCR merge (Phase 4). SRT/VTT/MD + `--format` (Phase 5). Runtime CUDA→CPU fallback.
