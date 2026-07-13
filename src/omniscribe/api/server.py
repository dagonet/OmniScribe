"""FastAPI server providing an HTTP API for OmniScribe transcription.

v1 non-goals
------------
- No persistence: restarting the server loses all jobs.
- No cancellation / job deletion.
- No authentication.
- No result formats other than JSON (output_format is hard-coded to "json").
- No file upload --- sources are URLs or local paths.
- No SSE / webhooks --- clients poll GET /jobs/{id}.
- Shutdown hang: the executor uses non-daemon threads, so Ctrl+C/uvicorn
  shutdown blocks until the in-flight job finishes. Acceptable v1 behaviour.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from omniscribe import __version__
from omniscribe.cli import process_single_video
from omniscribe.config import OmniScribeConfig
from omniscribe.errors import OmniScribeError

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


class JobStatus(StrEnum):
    """Status of a transcription job."""

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class JobRequest(BaseModel):
    """Request body for POST /jobs."""

    source: str
    language: str | None = None
    translate: bool | None = None
    ocr: bool | None = None
    ocr_language: str | None = None
    platform: str | None = None


class Job(BaseModel):
    """Represents a transcription job."""

    id: str
    source: str
    status: JobStatus
    created_at: datetime
    error: str | None = None
    result: dict[str, Any] | None = None


class _JobStore:
    """Thread-safe in-memory job store."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Atomically update a job's fields."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if error is not None:
                job.error = error
            if result is not None:
                job.result = result


class _Executor(Protocol):
    """Minimal executor protocol for dependency injection.

    Tests pass a synchronous executor so job state is deterministic at
    POST-return time; production uses ``ThreadPoolExecutor(max_workers=1)``.
    """

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...
    def shutdown(self, wait: bool = True) -> None: ...


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _run_job(
    job_id: str,
    source: str,
    request: JobRequest,
    base_config: OmniScribeConfig,
    base_temp: Path,
    store: _JobStore,
) -> None:
    """Run the transcription pipeline and store the result.

    Single-worker design: the executor's ``max_workers=1`` guarantees that
    only one job runs at a time, which matches GPU serialisation and avoids
    temp-dir clobber (each job gets a unique ``temp_dir`` below).
    """
    # Per-job temp_dir so concurrent jobs (even with max_workers=1 the
    # guarantee is architectural, not accidental) do not share workdirs.
    updates: dict[str, Any] = {"temp_dir": base_temp / job_id}
    if request.language is not None:
        updates["whisper_language"] = request.language
    if request.translate is not None:
        updates["whisper_task"] = "translate" if request.translate else "transcribe"
    if request.ocr_language is not None:
        updates["ocr_language"] = request.ocr_language
    if request.platform is not None:
        updates["platform_profile"] = request.platform

    cfg = base_config.model_copy(update=updates)
    ocr_active = request.ocr if request.ocr is not None else cfg.ocr_enabled

    # Output goes to a dedicated api-results subdirectory under the base
    # temp dir so it survives per-job temp_dir cleanup (process_single_video
    # deletes cfg.temp_dir in its finally block).
    output_dir = base_temp / "api-results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{job_id}.json"

    store.update(job_id, status=JobStatus.running)

    try:
        process_single_video(
            source,
            cfg,
            output_path,
            ocr_active=ocr_active,
            output_format="json",
        )
    except OmniScribeError as e:
        store.update(job_id, status=JobStatus.failed, error=str(e))
        return
    except Exception:
        logger.exception("Unexpected error processing job %s", job_id)
        store.update(job_id, status=JobStatus.failed, error="Internal pipeline error")
        return

    try:
        result_data: dict[str, Any] = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read pipeline output for job %s", job_id)
        store.update(job_id, status=JobStatus.failed, error="Failed to read pipeline output")
        return

    store.update(job_id, status=JobStatus.done, result=result_data)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    config: OmniScribeConfig | None = None,
    *,
    executor: _Executor | None = None,
) -> FastAPI:
    """Create a FastAPI application for the OmniScribe API.

    Parameters
    ----------
    config:
        Optional config override. Defaults to ``OmniScribeConfig()`` (env-only).
    executor:
        Optional executor override. Production default is
        ``ThreadPoolExecutor(max_workers=1)`` --- single worker because GPU
        serialisation and the per-job temp-dir contract both demand it.
        Pass a synchronous executor in tests so job state is final at
        POST-return time without polling.
    """
    if config is None:
        config = OmniScribeConfig()
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=1)

    store = _JobStore()
    # Resolve once so all per-job temp_dir values are absolute.
    base_temp = config.temp_dir.resolve()

    app = FastAPI(title="OmniScribe API", version=__version__)

    @app.get("/healthz")
    def _healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/jobs", status_code=202)
    def _submit_job(request: JobRequest) -> dict[str, str]:
        job = Job(
            id=uuid4().hex,
            source=request.source,
            status=JobStatus.queued,
            created_at=datetime.now(UTC),
        )
        store.add(job)
        executor.submit(_run_job, job.id, request.source, request, config, base_temp, store)
        return {"job_id": job.id}

    @app.get("/jobs")
    def _list_jobs() -> list[dict[str, str]]:
        return [
            {
                "id": j.id,
                "source": j.source,
                "status": j.status.value,
                "created_at": j.created_at.isoformat(),
            }
            for j in store.all()
        ]

    @app.get("/jobs/{job_id}")
    def _get_job(job_id: str) -> Job:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    return app
