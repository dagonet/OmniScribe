"""Tests for the Windows-only nvidia DLL preload shim in omniscribe.asr.whisper.

All tests must run on Linux CI without touching real Windows APIs. Use
``unittest.mock.patch("os.add_dll_directory", create=True)`` because
``os.add_dll_directory`` does not exist on POSIX.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from omniscribe.asr.whisper import _nvidia_dll_cookies, _register_nvidia_dll_dirs


def test_shim_noop_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-Windows, the shim must not register any DLL dirs."""
    # Force non-windows so the test is robust on Windows dev machines too.
    monkeypatch.setattr("sys.platform", "linux")
    # Snapshot module-scope state populated at import time; assert no growth
    # rather than asserting emptiness, to avoid coupling to import order.
    before = len(_nvidia_dll_cookies)
    _register_nvidia_dll_dirs()
    assert len(_nvidia_dll_cookies) == before


def test_shim_handles_missing_nvidia_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no nvidia dir is on sys.path, shim returns cleanly without preload."""
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.path", [])

    with (
        patch("os.add_dll_directory", create=True) as mock_add,
        patch("ctypes.CDLL") as mock_cdll,
    ):
        _register_nvidia_dll_dirs()

    mock_add.assert_not_called()
    mock_cdll.assert_not_called()


def test_shim_preloads_when_dlls_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """With a fake nvidia tree on sys.path, all DLLs preload in sorted-ASCII order.

    Covers cudart + cublas + cuDNN stub + 2 representative cuDNN sub-libs + cufft = 6 CDLL calls.
    Picking 2 representative cuDNN sub-libs (not all 8) avoids hard-coding today's
    NVIDIA cuDNN 9.21 layout into the test — a 9.22 rename of any sub-lib won't break this.
    """
    nvidia = tmp_path / "nvidia"
    cudart = nvidia / "cuda_runtime" / "bin" / "cudart64_12.dll"
    cublas = nvidia / "cublas" / "bin" / "cublas64_12.dll"
    cudnn_stub = nvidia / "cudnn" / "bin" / "cudnn64_9.dll"
    cudnn_ops = nvidia / "cudnn" / "bin" / "cudnn_ops64_9.dll"
    cudnn_graph = nvidia / "cudnn" / "bin" / "cudnn_graph64_9.dll"
    cufft = nvidia / "cufft" / "bin" / "cufft64_11.dll"
    for dll in (cudart, cublas, cudnn_stub, cudnn_ops, cudnn_graph, cufft):
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.touch()

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.path", [str(tmp_path)])

    with (
        patch("os.add_dll_directory", create=True) as mock_add,
        patch("ctypes.CDLL") as mock_cdll,
    ):
        _register_nvidia_dll_dirs()

    # Four bin/ dirs registered (cuda_runtime, cublas, cudnn, cufft).
    assert mock_add.call_count == 4
    # Six DLLs preloaded:
    # cudart -> cublas -> cudnn-stub -> cudnn_graph -> cudnn_ops -> cufft.
    # cuDNN sub-libs come out of sorted() in ASCII order: "_g" < "_o".
    assert mock_cdll.call_count == 6
    preloaded = [call.args[0] for call in mock_cdll.call_args_list]
    assert preloaded[0].endswith("cudart64_12.dll")
    assert preloaded[1].endswith("cublas64_12.dll")
    assert preloaded[2].endswith("cudnn64_9.dll")
    assert preloaded[3].endswith("cudnn_graph64_9.dll")
    assert preloaded[4].endswith("cudnn_ops64_9.dll")
    assert preloaded[5].endswith("cufft64_11.dll")


def test_shim_oserror_from_add_dll_directory_does_not_break_preload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If add_dll_directory raises OSError, the ctypes preload step still runs."""
    nvidia = tmp_path / "nvidia"
    cudart = nvidia / "cuda_runtime" / "bin" / "cudart64_12.dll"
    cublas = nvidia / "cublas" / "bin" / "cublas64_12.dll"
    cudnn = nvidia / "cudnn" / "bin" / "cudnn64_9.dll"
    for dll in (cudart, cublas, cudnn):
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.touch()

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("sys.path", [str(tmp_path)])

    with (
        patch("os.add_dll_directory", create=True, side_effect=OSError("simulated")),
        patch("ctypes.CDLL") as mock_cdll,
    ):
        # Must not raise — the OSError is suppressed by contextlib.suppress.
        _register_nvidia_dll_dirs()

    # Preload step still attempted at minimum for cudart (proving the
    # registration-step OSError didn't abort the function).
    assert mock_cdll.call_count >= 1
    preloaded = [call.args[0] for call in mock_cdll.call_args_list]
    assert any(p.endswith("cudart64_12.dll") for p in preloaded)
