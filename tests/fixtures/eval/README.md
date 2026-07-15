# Eval Samples Manifest

This directory holds fixtures for the opt-in `eval` integration test suite
(`pytest -m eval`). Media files and ground-truth JSONs live here on the local
machine but are **gitignored** — the repo tracks only this manifest, the
fetch script, and the test module. Third-party TikTok content never enters the
public repository (legal exposure + repository bloat).

## Samples

### Sample 1 — dense multi-column infographic (TikTok PHOTO, 8 slides)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@roadauravibes/photo/7658949262654410017` |
| Content class | Dense multi-column infographic photo post |
| Fixture path | `slides/sample-1/` (native JPG slides + optional audio) |
| Ground truth | `gt-sample-1.json` |
| Baseline recall | 1.0 (verified v0.1.7) |

### Sample 2 — headline slides (TikTok PHOTO, 7 slides)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@a.blackmirror/photo/7658362360523918625` |
| Content class | Headline-slides photo post |
| Fixture path | `slides/sample-2/` (native JPG slides + optional audio) |
| Ground truth | `gt-sample-2.json` |
| Baseline recall | 1.0 (verified v0.1.7) |

### Sample 3 — caption-overlay (TikTok VIDEO)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@antriebscode/video/7651478445557320993` |
| Content class | Caption-overlay video |
| Fixture path | `videos/sample-3.mp4` |
| Ground truth | `gt-sample-3.json` |
| Baseline recall | 1.0 (verified v0.1.7) |

### Sample 4 — clean multi-paragraph text slides (TikTok PHOTO, EN)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@st.felico/photo/7634604637898689799` |
| Content class | Clean multi-paragraph text slides (EN photo post) |
| Fixture path | `slides/sample-4/` (native JPG slides + audio) |
| Ground truth | `gt-sample-4.json` |
| Baseline recall | 1.0 (measured v0.2.5, raw_bboxes=60) |

### Sample 5 — stylized handwritten-style caps on textured pencil-art (TikTok PHOTO, DE)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@zitatbomben/photo/7640230807587605793` |
| Content class | Stylized handwritten-style caps on textured pencil-art (DE photo post — hardest OCR class, umlauts) |
| Fixture path | `slides/sample-5/` (native JPG slides + audio) |
| Ground truth | `gt-sample-5.json` |
| Baseline recall | 1.0 (measured v0.2.5, raw_bboxes=75) |

### Sample 6 — animated-text explainer video (TikTok VIDEO, EN)

| Field | Value |
|---|---|
| Source URL | `https://www.tiktok.com/@mindshiftdaily022/video/7639000032569462030` |
| Content class | Animated-text explainer video (EN, 8:51, persistent title banner) |
| Fixture path | `videos/sample-6.mp4` (h264 720p, aac audio) |
| Ground truth | `gt-sample-6.json` |
| Baseline recall | 0.60 (measured v0.2.5, raw_bboxes=176) |

## Notes

- **TikTok bytevc1 / 1080p video-only quirk**: TikTok's bytevc1 and 1080p format variants can be video-only despite metadata claiming aac audio. Sample-6 uses the `h264_720p_*-0` variant which carries audio.
- **gallery-dl slide filenames**: gallery-dl names downloaded slides after the post caption. These names may contain emoji or umlaut characters — supported since the Sprint 11 unicode-safe image-read fix.

## Ground Truth Schema

Ground truth files are JSON conforming to the `GroundTruth` pydantic model
(`src/omniscribe/eval/models.py`):

```json
{
  "language": "en",
  "expected_texts": [
    {
      "text": "Hello World",
      "start": 0.0,
      "end": 5.0,
      "required": true
    },
    {
      "text": "SUBSCRIBE",
      "required": false
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `language` | `str` | ISO 639-1 language code of the on-screen text |
| `expected_texts` | `list` | Array of texts expected to appear in OCR output |
| `expected_texts[].text` | `str` | The exact on-screen text (case-sensitive matching via fuzzy ratio) |
| `expected_texts[].start` | `float\|null` | Optional start time in seconds; filters OCR segments outside this window |
| `expected_texts[].end` | `float\|null` | Optional end time in seconds |
| `expected_texts[].required` | `bool` | If true, a match is required for 100% recall (default: true) |

> The GT JSON schema is documented by the `GroundTruth` pydantic model at
> `src/omniscribe/eval/models.py`. The `score_video` function at
> `src/omniscribe/eval/scoring.py` is the authoritative consumer.

## Creating / Updating Ground Truth

1. Watch the source content (URLs above) and note every text string that appears
   on screen, along with its approximate start/end times.
2. Write a JSON file per sample following the schema above. Include every
   distinct text string visible — even platform chrome (SUBSCRIBE, @user, etc.)
   as `"required": false` entries so they don't lower recall but do keep
   precision honest.
3. Place the files at `tests/fixtures/eval/gt-sample-{1,2,3,4,5,6}.json`.
4. Run the eval suite: `uv run pytest -m eval -v`.

## Fetching Fixtures

Run the fetch script from the repository root:

```bash
# Fetch all six samples:
python scripts/fetch_eval_samples.py

# Fetch a single sample:
python scripts/fetch_eval_samples.py --sample 6
```

The script is idempotent: it skips any sample whose target files already
exist. Photo samples (1, 2, 4, 5) require the `[photo]` extra (gallery-dl);
video samples (3, 6) use yt-dlp (bundled).
