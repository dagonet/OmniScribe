# Configuration Reference

OmniScribe is configured via environment variables, a `.env` file, or CLI flags.
All configuration is validated at startup by `OmniScribeConfig` (a
`pydantic-settings` `BaseSettings`).

## Loading order

1. `OmniScribeConfig()` is constructed at CLI startup in the `main` callback.
2. `pydantic-settings` reads `OMNI_`-prefixed environment variables, then falls
   back to a `.env` file in the current working directory.
3. CLI flags override config values via `_apply_cli_overrides()` in `cli.py`,
   which creates a `model_copy(update=...)` with the non-None flag values.

### Output format resolution

Output format is resolved separately in `pipeline._resolve_output_format()` with
its own precedence chain:

1. `--format` CLI flag (highest)
2. `OMNI_OUTPUT_FORMAT` env var (if set — presence triggers this branch)
3. Output-path extension (`.json` / `.txt` / `.srt` / `.md`)
4. Hard default `"json"`

## Field table

### ASR

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `whisper_model` | `OMNI_WHISPER_MODEL` | `"large-v3-turbo"` | Faster-Whisper model size or path |
| `whisper_device` | `OMNI_WHISPER_DEVICE` | `"cuda"` | Torch device for Whisper inference |
| `whisper_compute_type` | `OMNI_WHISPER_COMPUTE_TYPE` | `"float16"` | Compute precision (`"float16"`, `"float32"`, `"int8_float16"`, etc.) |
| `whisper_batch_size` | `OMNI_WHISPER_BATCH_SIZE` | `16` | Batched inference batch size |
| `whisper_language` | `OMNI_WHISPER_LANGUAGE` | `None` | Force ASR language (e.g. `"en"`); `None` = auto-detect |
| `whisper_task` | `OMNI_WHISPER_TASK` | `"transcribe"` | `"transcribe"` or `"translate"` (translate speech to English) |

### OCR

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `ocr_enabled` | `OMNI_OCR_ENABLED` | `True` | Master switch for the OCR pipeline |
| `ocr_language` | `OMNI_OCR_LANGUAGE` | `"auto"` | RapidOCR `LangRec` value, `"auto"` (resolve from ASR language), or ISO 639-1 code |
| `ocr_mask_auto_captions` | `OMNI_OCR_MASK_AUTO_CAPTIONS` | `True` | When `True`, the auto-caption zone from the platform profile is masked (excluded from OCR) |
| `ocr_sample_fps` | `OMNI_OCR_SAMPLE_FPS` | `1.0` | Frame sampling rate (frames per second of video) |
| `ocr_min_confidence` | `OMNI_OCR_MIN_CONFIDENCE` | `0.6` | Minimum OCR confidence score (0.0–1.0) for a detection to be kept |
| `ocr_device` | `OMNI_OCR_DEVICE` | `"cuda"` | ONNX Runtime device for RapidOCR (`"cuda"` or `"cpu"`) |
| `scene_change_enabled` | `OMNI_SCENE_CHANGE_ENABLED` | `True` | Enable scene-change detection in the frame sampler |
| `scene_change_threshold` | `OMNI_SCENE_CHANGE_THRESHOLD` | `0.02` | Normalised mean-absdiff threshold for scene-change detection (0.0–1.0) |
| `ocr_frequency_min_frame_count` | `OMNI_OCR_FREQUENCY_MIN_FRAME_COUNT` | `10` | Minimum frame count for the frequency filter to activate (guard for short clips) |
| `ocr_det_limit_side_len` | `OMNI_OCR_DET_LIMIT_SIDE_LEN` | `None` | RapidOCR Det limit_side_len override (≥ 32, or `None` for config default) |
| `ocr_det_thresh` | `OMNI_OCR_DET_THRESH` | `None` | RapidOCR Det detection threshold (0.0–1.0, or `None` for default) |
| `ocr_det_box_thresh` | `OMNI_OCR_DET_BOX_THRESH` | `None` | RapidOCR Det box threshold (0.0–1.0, or `None` for default) |
| `ocr_det_model_type` | `OMNI_OCR_DET_MODEL_TYPE` | `None` | Detection model variant (`"mobile"` or `"server"`; `None` = rapidocr default) |
| `ocr_det_ocr_version` | `OMNI_OCR_DET_OCR_VERSION` | `None` | Detection OCR version (`"pp-ocrv4"` or `"pp-ocrv5"`; `None` = default) |
| `ocr_rec_model_type` | `OMNI_OCR_REC_MODEL_TYPE` | `None` | Recognition model variant (`"mobile"` or `"server"`; `None` = rapidocr default) |
| `ocr_rec_ocr_version` | `OMNI_OCR_REC_OCR_VERSION` | `None` | Recognition OCR version (`"pp-ocrv4"` or `"pp-ocrv5"`; `None` = default) |

### LLM cleanup

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `llm_cleanup_enabled` | `OMNI_LLM_CLEANUP_ENABLED` | `False` | Enable Ollama-backed OCR artefact cleanup on ON-SCREEN and BOTH segments |
| `llm_cleanup_model` | `OMNI_LLM_CLEANUP_MODEL` | `"llama3.2:3b"` | Ollama model name for cleanup |
| `llm_cleanup_host` | `OMNI_LLM_CLEANUP_HOST` | `"http://localhost:11434"` | Ollama server URL |
| `llm_cleanup_timeout_s` | `OMNI_LLM_CLEANUP_TIMEOUT_S` | `30.0` | HTTP request timeout for Ollama calls (must be > 0) |
| `llm_cleanup_keep_alive_s` | `OMNI_LLM_CLEANUP_KEEP_ALIVE_S` | `300.0` | Ollama model keep-alive duration after last call (≥ -1.0; -1 = forever) |
| `llm_asr_cleanup_enabled` | `OMNI_LLM_ASR_CLEANUP_ENABLED` | `False` | Enable Ollama-backed punctuation + capitalisation cleanup on SPEECH segments |

### Platform

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `platform_profile` | `OMNI_PLATFORM_PROFILE` | `"auto"` | Platform profile name (`"auto"`, `"tiktok"`, `"youtube"`, `"instagram"`, `"generic"`) |
| `ui_filter_enabled` | `OMNI_UI_FILTER_ENABLED` | `True` | Enable zone masking, pattern filtering, and frequency filtering |

### Dedup

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `dedup_similarity_threshold` | `OMNI_DEDUP_SIMILARITY_THRESHOLD` | `0.85` | RapidFuzz `WRatio` threshold (0.0–1.0) for same-source OCR dedup |
| `dedup_min_duration` | `OMNI_DEDUP_MIN_DURATION` | `0.0` | Minimum duration in seconds for a dedup cluster span (≥ 0.0) |

### Merge

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `merge_similarity_threshold` | `OMNI_MERGE_SIMILARITY_THRESHOLD` | `0.85` | RapidFuzz `WRatio` threshold (0.0–1.0) for cross-source SPEECH+OCR merge |

### Output

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `output_format` | `OMNI_OUTPUT_FORMAT` | `"json"` | Default output format (`"json"`, `"txt"`, `"srt"`, `"md"`) |

### General

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `temp_dir` | `OMNI_TEMP_DIR` | `{tempdir}/omniscribe` | Working directory for downloads and intermediate files |
| `keep_temp_files` | `OMNI_KEEP_TEMP_FILES` | `False` | If `True`, temp files are not cleaned up after processing |
| `log_level` | `OMNI_LOG_LEVEL` | `"INFO"` | Logging level (`"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`) |

## Config naming convention

Every field maps to `OMNI_` + the uppercased field name. For example,
`ocr_sample_fps` becomes `OMNI_OCR_SAMPLE_FPS`. Nested or composed field names
use underscores in the env var as they do in the Python field name.

## CLI override mechanism

Boolean flags (`--ocr/--no-ocr`, `--ui-filter/--no-ui-filter`,
`--scene-change/--no-scene-change`, `--llm-cleanup/--no-llm-cleanup`,
`--asr-cleanup/--no-asr-cleanup`, `--translate/--no-translate`) set or clear
the corresponding config field when explicitly passed. String options
(`--language`, `--ocr-language`, `--platform`) override the matching field.
`--ocr` is handled separately as a pipeline parameter rather than a config field
override.

## Validation rules

| Field(s) | Rule | Error condition |
|---|---|---|
| `whisper_language`, `ocr_det_limit_side_len`, `ocr_det_thresh`, `ocr_det_box_thresh`, `ocr_det_model_type`, `ocr_det_ocr_version`, `ocr_rec_model_type`, `ocr_rec_ocr_version` | Coerces empty-string env values to `None` | — (empty string treated as unset) |
| `platform_profile` | Must be one of `auto`, `tiktok`, `youtube`, `instagram`, `unknown`, `generic` | Unknown value rejected with list of allowed options |
| `scene_change_threshold` | Must be in `(0.0, 1.0]` | `0.0` (defeats feature) or `> 1.0` (impossible) rejected |
| `llm_cleanup_timeout_s` | Must be `> 0` | Zero or negative values rejected |
| `llm_cleanup_keep_alive_s` | Must be `≥ -1.0` | Values below `-1.0` rejected |
| `output_format` | Must be one of `json`, `txt`, `srt`, `md` | Unknown value rejected with list of allowed options |
| `dedup_min_duration` | Must be `≥ 0.0` | Negative values rejected |
| `ocr_language` | Must be a valid `LangRec` member, `"auto"`, or a mapped ISO 639-1 code | Unmapped arbitrary strings rejected before engine init |
| `ocr_det_model_type`, `ocr_rec_model_type` | Must be `"mobile"` or `"server"` (case-insensitive), or `None` | Unknown values rejected |
| `ocr_det_ocr_version`, `ocr_rec_ocr_version` | Must be `"pp-ocrv4"` or `"pp-ocrv5"` (case-insensitive), or `None` | Unknown values rejected |
| `merge_similarity_threshold` | Must be in `[0.0, 1.0]` | Out-of-range values rejected |
