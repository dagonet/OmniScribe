# Project Context

## Project

- **Name**: OmniScribe
- **Repository**: https://github.com/dagonet/OmniScribe
- **Tech Stack**: Python 3.11, uv

## Build System

- **Build Command**: uv run pytest
- **Test Command**: uv run pytest
- **Format Command**: uv run ruff format .
- **Lint Command**: uv run ruff check .
- **Gate Command**: {{GATE_COMMAND}}

## Paths

- **Source Root**: src/  <!-- or omniscribe/ — adjust to project layout -->
- **Test Root**: tests/
- **Worktree Base**: g:/git/.worktrees
- **Log Path**: logs/

## Docker

- **Build Image**: `docker build -t omniscribe .`
- **Run CLI**: `docker run --rm omniscribe --help`
- **Transcribe (GPU)**: `docker run --gpus all --rm -v ./input:/input -v ./output:/output omniscribe transcribe /input/video.mp4 -o /output/transcript.json`
- **Transcribe (CPU)**: `docker run --rm -v ./input:/input -v ./output:/output -e OMNI_WHISPER_DEVICE=cpu -e OMNI_OCR_DEVICE=cpu omniscribe transcribe /input/video.mp4 -o /output/transcript.json`
- **Prerequisites**: NVIDIA Container Toolkit (GPU), Docker 20.10+

## Workflow Configuration

- **Task source**: `plan-files`
- **Max parallel workstreams**: 5
- **Commit convention**: `feat:`, `fix:`, `chore:`, `test:`, `docs:` prefixes
- **Issue labels** (github-issues mode only): `feature`, `bug`, `tech-debt`

## Preprocessing

- **Ollama**: available (MCP: `ollama-tools`) -- see CLAUDE.local.md for usage rules
- **Context7**: available (MCP: `context7`)