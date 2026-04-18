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
- **Python Version**: 3.11

## Paths

- **Source Root**: src/  <!-- or omniscribe/ — adjust to project layout -->
- **Test Root**: tests/
- **Worktree Base**: g:/git/.worktrees
- **Log Path**: logs/

## Workflow Configuration

- **Task source**: `plan-files`
- **Max parallel workstreams**: 5
- **Commit convention**: `feat:`, `fix:`, `chore:`, `test:`, `docs:` prefixes
- **Issue labels** (github-issues mode only): `feature`, `bug`, `tech-debt`

## Preprocessing

- **Ollama**: available (MCP: `ollama-tools`) -- see CLAUDE.local.md for usage rules
- **Context7**: available (MCP: `context7`)