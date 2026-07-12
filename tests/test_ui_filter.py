"""Unit tests for omniscribe.ocr.ui_filter.

Covers the binding (a)-(k) scenarios from Sprint 3.2:
zone masking (empty/full/half/overlapping), pattern filtering
(drop handles, pass SPEECH, drop empty), and frequency filtering
(ratio boundaries, zero-frame short-circuit).
"""

from __future__ import annotations

import re

import numpy as np
import pytest

from omniscribe.ocr.ui_filter import (
    filter_by_frequency,
    filter_by_patterns,
    mask_zones,
)
from omniscribe.output import TranscriptSegment
from omniscribe.platforms import TIKTOK_PROFILE
from omniscribe.platforms.base import RelativeRect


def _ocr(text: str) -> TranscriptSegment:
    return TranscriptSegment(start=0.0, end=0.0, text=text, source="ON-SCREEN", language="en")


def _speech(text: str) -> TranscriptSegment:
    return TranscriptSegment(start=0.0, end=1.0, text=text, source="SPEECH", language="en")


# --- (a) mask_zones: empty zones is a no-op --------------------------------
def test_mask_zones_empty_zones_returns_input_unchanged() -> None:
    gray = np.full((4, 4), 200, dtype=np.uint8)
    result = mask_zones(gray, ())
    # No copy, no mutation.
    assert result is gray
    assert np.array_equal(result, np.full((4, 4), 200, dtype=np.uint8))


# --- (b) mask_zones: single full-frame zone zeros the whole image ----------
def test_mask_zones_full_frame_zone_zeros_everything() -> None:
    gray = np.full((10, 8), 255, dtype=np.uint8)
    result = mask_zones(gray, (RelativeRect(0.0, 0.0, 1.0, 1.0),))
    assert np.array_equal(result, np.zeros((10, 8), dtype=np.uint8))
    # Defensive copy: the input stays pristine.
    assert gray[0, 0] == 255


# --- (c) mask_zones: right-half zone leaves left half untouched ------------
def test_mask_zones_right_half_zones_right_half_only() -> None:
    gray = np.full((4, 10), 128, dtype=np.uint8)
    result = mask_zones(gray, (RelativeRect(0.5, 0.0, 0.5, 1.0),))
    assert np.array_equal(result[:, :5], np.full((4, 5), 128, dtype=np.uint8))
    assert np.array_equal(result[:, 5:], np.zeros((4, 5), dtype=np.uint8))


# --- (d) mask_zones: overlapping zones — union is all zero, no crash ------
def test_mask_zones_overlapping_zones_cover_union() -> None:
    gray = np.full((6, 6), 200, dtype=np.uint8)
    zones = (
        RelativeRect(0.0, 0.0, 0.7, 1.0),
        RelativeRect(0.3, 0.0, 0.7, 1.0),
    )
    result = mask_zones(gray, zones)
    assert np.array_equal(result, np.zeros((6, 6), dtype=np.uint8))


# --- (e) filter_by_patterns drops handle, keeps embedded handle -----------
def test_filter_by_patterns_drops_handle_keeps_embedded_handle() -> None:
    patterns = (re.compile(r"^@\w+$"),)
    segs = [_ocr("@user"), _ocr("hello @user")]
    result = filter_by_patterns(segs, patterns)
    assert [s.text for s in result] == ["hello @user"]


# --- (f) filter_by_patterns passes SPEECH through even when text matches --
def test_filter_by_patterns_passes_speech_through_even_when_matching() -> None:
    patterns = (re.compile(r"^@\w+$"),)
    segs = [_speech("@anchor"), _ocr("@user")]
    result = filter_by_patterns(segs, patterns)
    assert [s.source for s in result] == ["SPEECH"]
    assert result[0].text == "@anchor"


# --- (g) filter_by_patterns with r"^$" drops empty ON-SCREEN, keeps SPEECH
def test_filter_by_patterns_empty_text_regex_drops_only_onscreen() -> None:
    patterns = (re.compile(r"^$"),)
    segs = [_ocr(""), _speech(""), _ocr("kept")]
    result = filter_by_patterns(segs, patterns)
    assert [(s.source, s.text) for s in result] == [("SPEECH", ""), ("ON-SCREEN", "kept")]


# --- (h) filter_by_frequency drops text at ratio 3/3 with threshold 0.8 ---
def test_filter_by_frequency_drops_at_ratio_one_with_threshold_below_one() -> None:
    segs = [_ocr("SUBSCRIBE"), _ocr("SUBSCRIBE"), _ocr("SUBSCRIBE")]
    result = filter_by_frequency(segs, frame_count=3, threshold=0.8)
    assert result == []


# --- (i) filter_by_frequency keeps text at ratio 1/3 with threshold 0.8 ---
def test_filter_by_frequency_keeps_transient_text() -> None:
    segs = [_ocr("transient"), _ocr("other"), _ocr("third")]
    result = filter_by_frequency(segs, frame_count=3, threshold=0.8)
    assert [s.text for s in result] == ["transient", "other", "third"]


# --- (j) filter_by_frequency: frame_count=0 short-circuits ----------------
def test_filter_by_frequency_frame_count_zero_passes_through() -> None:
    segs = [_ocr("anything"), _ocr("anything")]
    result = filter_by_frequency(segs, frame_count=0, threshold=0.5)
    assert [s.text for s in result] == ["anything", "anything"]


# --- (k) filter_by_frequency with frame_count=1 and ratio=1.0 drops -------
def test_filter_by_frequency_single_frame_ratio_one_drops() -> None:
    segs = [_ocr("WATERMARK")]
    result = filter_by_frequency(segs, frame_count=1, threshold=0.95)
    assert result == []


# --- extra: case-fold normalisation collapses SUBSCRIBE/Subscribe ---------
def test_filter_by_frequency_case_folds_internally() -> None:
    segs = [_ocr("SUBSCRIBE"), _ocr("Subscribe"), _ocr("subscribe")]
    result = filter_by_frequency(segs, frame_count=3, threshold=0.95)
    assert result == []


# --- extra: speech bypasses frequency filter ------------------------------
def test_filter_by_frequency_speech_passes_through() -> None:
    segs = [_speech("hello"), _ocr("WATERMARK"), _ocr("WATERMARK")]
    result = filter_by_frequency(segs, frame_count=2, threshold=0.95)
    assert [s.source for s in result] == ["SPEECH"]


# --- extra: empty patterns tuple returns copy of input --------------------
def test_filter_by_patterns_empty_patterns_returns_copy_of_input() -> None:
    segs = [_ocr("a"), _ocr("b")]
    result = filter_by_patterns(segs, ())
    assert result == segs
    assert result is not segs


# --- Sprint 7.1 (a) fuzzy clustering: SUBSCRIBE family collapses ----------
def test_filter_by_frequency_fuzzy_clusters_subscribe_family() -> None:
    # Three near-duplicate prompts each appearing once across 3 frames.
    # Exact-Counter (pre-7.1) would see 3 distinct keys at ratio 1/3 each
    # (all kept). Fuzzy clustering at fuzzy_threshold=90 combines them to
    # one cluster with count 3 / 3 frames = 1.0 ratio, dropped at
    # threshold=0.95.
    #
    # Each pair clears 90 under rapidfuzz.fuzz.ratio:
    # SUBSCRIBE!/Subscribe. = 90.0; SUBSCRIBE!/SUBSCRIBE = 94.7;
    # Subscribe./SUBSCRIBE = 94.7. (The arrow-variant "Subscribe →" is
    # at 85.7 vs "SUBSCRIBE!" — single-link greedy can still bridge it
    # via the "SUBSCRIBE" hub, but the assertion below uses the simpler
    # all-pairs-match triple to keep the test robust.)
    segs = [_ocr("SUBSCRIBE!"), _ocr("Subscribe."), _ocr("SUBSCRIBE")]
    result = filter_by_frequency(
        segs,
        frame_count=3,
        threshold=0.95,
        fuzzy_threshold=90.0,
    )
    assert result == []


# --- Sprint 7.1 (b) fuzzy negative: distinct text stays in three buckets --
def test_filter_by_frequency_fuzzy_keeps_distinct_text() -> None:
    # Three unrelated strings — none above fuzzy_threshold=90 vs each
    # other. Each ends up in its own cluster at 1/3, below 0.95 drop.
    segs = [_ocr("Hello world"), _ocr("Goodbye sun"), _ocr("Random text")]
    result = filter_by_frequency(
        segs,
        frame_count=3,
        threshold=0.95,
        fuzzy_threshold=90.0,
    )
    assert [s.text for s in result] == ["Hello world", "Goodbye sun", "Random text"]


# --- Sprint 7.1 (c) mask integration: TikTok caption band is zeroed -------
def test_mask_zones_tiktok_profile_zeros_caption_band() -> None:
    # 1080x1920 all-white synthetic frame run through the live TikTok
    # profile. The auto-caption band (now on ``auto_caption_zones``, mid-lower
    # screen) MUST contain a zeroed pixel when the combined mask zones include
    # it. We pick a representative pixel inside the documented band y∈[0.55, 0.78].
    height, width = 1920, 1080
    gray = np.full((height, width), 255, dtype=np.uint8)
    all_zones = TIKTOK_PROFILE.ui_exclusion_zones + TIKTOK_PROFILE.auto_caption_zones
    masked = mask_zones(gray, all_zones)
    # Sample point at roughly y=0.65 (mid-band), x=0.5 (centre).
    sample_y = int(0.65 * height)
    sample_x = int(0.5 * width)
    assert masked[sample_y, sample_x] == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
