"""Faster-whisper transcriber (lazy-loaded, GPU-friendly)."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# ``os.add_dll_directory()`` returns a cookie; the directory is removed from
# the DLL search path when the cookie is garbage-collected.  Keep references
# alive for the lifetime of the process so CTranslate2 can find cublas64_12.dll
# at inference time.
_nvidia_dll_cookies: list[object] = []


def _register_nvidia_dll_dirs() -> None:
    """Register nvidia CUDA DLL directories and preload critical DLLs on Windows.

    nvidia-* pip packages (cublas, cudnn, etc.) ship as namespace packages
    without ``__init__.py``, so their ``bin/`` directories are never added to
    the DLL search path.  CTranslate2 needs ``cublas64_12.dll`` at inference
    time, and ``os.add_dll_directory()`` is the only way to make DLL
    directories visible.

    ``os.add_dll_directory`` alone is not enough: when CTranslate2 calls
    ``LoadLibrary("cublas64_12.dll")``, Windows must resolve transitive
    dependencies (e.g. ``cudart64_12.dll``) across *different* registered
    directories.  The loader does not always walk all registered directories
    when chasing transitive deps.  Explicitly preloading the critical DLLs
    via ctypes guarantees they are resident before CTranslate2 ever asks for
    them.
    """
    if sys.platform != "win32":
        return

    import ctypes

    n_dirs = 0
    n_preloaded = 0

    for p in sys.path:
        nvidia_base = Path(p) / "nvidia"
        if not nvidia_base.is_dir():
            continue

        # Step 1 — register every nvidia/*/bin directory.
        bin_dirs: dict[str, Path] = {}
        for child in nvidia_base.iterdir():
            bin_dir = child / "bin"
            if bin_dir.is_dir():
                with contextlib.suppress(OSError):
                    _nvidia_dll_cookies.append(os.add_dll_directory(str(bin_dir)))
                    n_dirs += 1
                bin_dirs[child.name] = bin_dir

        # Step 2 — preload cublas/cudnn/cufft and their transitive deps.
        # Order matters: cudart must load first (cublas links against it),
        # cudnn must load after cublas (cudnn links against cublas), and
        # cufft is independent but kept last so the order matches the
        # documented preload sequence.  cufft is required by
        # onnxruntime-gpu's CUDAExecutionProvider (used by RapidOCR);
        # without it, ORT silently falls back to CPU.
        _preload = bin_dirs.get("cuda_runtime", Path()) / "cudart64_12.dll"
        if _preload.exists():
            with contextlib.suppress(OSError):
                ctypes.CDLL(str(_preload))
                n_preloaded += 1

        _preload = bin_dirs.get("cublas", Path()) / "cublas64_12.dll"
        if _preload.exists():
            with contextlib.suppress(OSError):
                ctypes.CDLL(str(_preload))
                n_preloaded += 1

        _preload = bin_dirs.get("cudnn", Path()) / "cudnn64_9.dll"
        if _preload.exists():
            with contextlib.suppress(OSError):
                ctypes.CDLL(str(_preload))
                n_preloaded += 1

        _preload = bin_dirs.get("cufft", Path()) / "cufft64_11.dll"
        if _preload.exists():
            with contextlib.suppress(OSError):
                ctypes.CDLL(str(_preload))
                n_preloaded += 1

    logger.debug(
        "nvidia DLL shim: registered %d dir(s), preloaded %d DLL(s)",
        n_dirs,
        n_preloaded,
    )


_register_nvidia_dll_dirs()

from faster_whisper import (  # noqa: E402 (must follow DLL registration)
    BatchedInferencePipeline,
    WhisperModel,
)

from omniscribe.output import TranscriptSegment  # noqa: E402 (must follow DLL registration)

if TYPE_CHECKING:
    from omniscribe.config import OmniScribeConfig


class WhisperTranscriber:
    """Wraps :class:`WhisperModel` + :class:`BatchedInferencePipeline` with lazy init."""

    def __init__(self, config: OmniScribeConfig) -> None:
        self._config = config
        self._pipeline: BatchedInferencePipeline | None = None

    def _ensure_loaded(self) -> BatchedInferencePipeline:
        if self._pipeline is None:
            logger.info(
                "Loading Whisper model %s on %s (compute_type=%s) — first run may download ~1.5 GB",
                self._config.whisper_model,
                self._config.whisper_device,
                self._config.whisper_compute_type,
            )
            model = WhisperModel(
                model_size_or_path=self._config.whisper_model,
                device=self._config.whisper_device,
                compute_type=self._config.whisper_compute_type,
            )
            self._pipeline = BatchedInferencePipeline(model)
        return self._pipeline

    def transcribe(self, audio_path: Path) -> tuple[list[TranscriptSegment], str]:
        """Run ASR on ``audio_path``; return (segments, detected_language)."""
        pipeline = self._ensure_loaded()
        segments_gen, info = pipeline.transcribe(
            str(audio_path),
            language=self._config.whisper_language,
            batch_size=self._config.whisper_batch_size,
            vad_filter=True,
            word_timestamps=False,
        )
        segments = [
            TranscriptSegment(
                start=float(s.start),
                end=float(s.end),
                text=s.text.strip(),
                confidence=getattr(s, "avg_logprob", None),
                language=info.language,
            )
            for s in segments_gen
        ]
        return segments, info.language
