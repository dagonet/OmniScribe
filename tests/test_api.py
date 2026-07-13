"""Tests for the HTTP API server."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from omniscribe.api.server import create_app
from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError


class _SyncFuture:
    """Placeholder future for synchronous execution."""

    def result(self, timeout: float | None = None) -> None:
        return None


class _SyncExecutor:
    """Runs submitted functions inline — job state is final at POST-return."""

    def submit(self, fn, /, *args, **kwargs):
        fn(*args, **kwargs)
        return _SyncFuture()

    def shutdown(self, wait: bool = True) -> None:
        pass


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def sync_executor():
    """Return a synchronous executor that runs jobs inline."""
    return _SyncExecutor()


@pytest.fixture
def test_config(tmp_path: Path) -> OmniScribeConfig:
    """Return a config with a temp dir under tmp_path."""
    return OmniScribeConfig(_env_file=None, temp_dir=str(tmp_path / "omni"))


@pytest.fixture
def client(sync_executor, test_config: OmniScribeConfig) -> TestClient:
    """Return a TestClient with a synchronous executor."""
    app = create_app(config=test_config, executor=sync_executor)
    return TestClient(app)


def _fake_transcribe_success(source, config, output_path, *, ocr_active, output_format):
    """Mock side_effect: writes valid transcript JSON to output_path."""
    data = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "hello", "language": "en", "source": "SPEECH"}
        ],
        "language": "de",
    }
    output_path.write_text(json.dumps(data), encoding="utf-8")


# ── Tests ──────────────────────────────────────────────────────────────────


def test_healthz_returns_ok_and_version(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)


def test_submit_job_returns_202_with_job_id(client: TestClient) -> None:
    with patch("omniscribe.api.server.process_single_video", side_effect=_fake_transcribe_success):
        resp = client.post("/jobs", json={"source": "fake.mp4"})

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)


def test_submitted_job_appears_in_list(client: TestClient) -> None:
    with patch("omniscribe.api.server.process_single_video", side_effect=_fake_transcribe_success):
        post_resp = client.post("/jobs", json={"source": "fake.mp4"})
    job_id = post_resp.json()["job_id"]

    list_resp = client.get("/jobs")
    assert list_resp.status_code == 200
    ids = [j["id"] for j in list_resp.json()]
    assert job_id in ids


def test_job_lifecycle_happy_path(client: TestClient) -> None:
    """Job goes through queued -> running -> done, result is populated."""
    with patch("omniscribe.api.server.process_single_video", side_effect=_fake_transcribe_success):
        post_resp = client.post("/jobs", json={"source": "fake.mp4"})
    job_id = post_resp.json()["job_id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["result"] is not None
    assert body["result"]["language"] == "de"
    assert len(body["result"]["segments"]) == 1


def test_job_failed_on_omniscribe_error(client: TestClient) -> None:
    def _fail(source, config, output_path, *, ocr_active, output_format):
        raise OmniScribeError("ffmpeg not found")

    with patch("omniscribe.api.server.process_single_video", side_effect=_fail):
        post_resp = client.post("/jobs", json={"source": "fake.mp4"})
    job_id = post_resp.json()["job_id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "ffmpeg not found"
    assert body["result"] is None


def test_job_failed_on_unexpected_exception(client: TestClient) -> None:
    def _crash(source, config, output_path, *, ocr_active, output_format):
        raise ValueError("something broke")

    with patch("omniscribe.api.server.process_single_video", side_effect=_crash):
        post_resp = client.post("/jobs", json={"source": "fake.mp4"})
    job_id = post_resp.json()["job_id"]

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    # Unexpected exceptions yield a generic message (traceback logged).
    assert "Internal pipeline error" in body["error"]
    assert body["result"] is None


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get("/jobs/nonexistent")
    assert resp.status_code == 404


def test_request_language_maps_to_whisper_language(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    """language field maps to whisper_language on config."""
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "language": "fr"})

    assert len(captured_configs) == 1
    assert captured_configs[0].whisper_language == "fr"


def test_request_translate_maps_to_whisper_task(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    """translate=True maps to whisper_task=translate."""
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "translate": True})

    assert len(captured_configs) == 1
    assert captured_configs[0].whisper_task == "translate"


def test_request_translate_false_maps_to_transcribe(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    """translate=False maps to whisper_task=transcribe."""
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "translate": False})

    assert len(captured_configs) == 1
    assert captured_configs[0].whisper_task == "transcribe"


def test_request_ocr_false_disables_ocr(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    """ocr=False sets ocr_active to False in the worker."""
    captured_args: list[dict] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_args.append({"ocr_active": ocr_active})
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "ocr": False})

    assert len(captured_args) == 1
    assert captured_args[0]["ocr_active"] is False


def test_request_ocr_language_maps_to_config(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "ocr_language": "ch"})

    assert len(captured_configs) == 1
    assert captured_configs[0].ocr_language == "ch"


def test_request_platform_maps_to_platform_profile(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake.mp4", "platform": "tiktok"})

    assert len(captured_configs) == 1
    assert captured_configs[0].platform_profile == "tiktok"


def test_per_job_temp_dir_is_distinct(
    client: TestClient, sync_executor, test_config: OmniScribeConfig
) -> None:
    """Two sequential jobs get distinct temp_dir values under the base temp."""
    captured_configs: list[OmniScribeConfig] = []

    def _capture(source, cfg, output_path, *, ocr_active, output_format):
        captured_configs.append(cfg)
        _fake_transcribe_success(
            source, cfg, output_path, ocr_active=ocr_active, output_format=output_format
        )

    app = create_app(config=test_config, executor=sync_executor)
    client = TestClient(app)

    with patch("omniscribe.api.server.process_single_video", side_effect=_capture):
        client.post("/jobs", json={"source": "fake1.mp4"})
        client.post("/jobs", json={"source": "fake2.mp4"})

    assert len(captured_configs) == 2
    assert captured_configs[0].temp_dir != captured_configs[1].temp_dir


def test_submit_without_source_returns_422(client: TestClient) -> None:
    resp = client.post("/jobs", json={})
    assert resp.status_code == 422
