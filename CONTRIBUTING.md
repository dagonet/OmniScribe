# Contributing to OmniScribe

Thanks for your interest in contributing! OmniScribe is in early development and welcomes contributions of all kinds.

## Ways to Contribute

- **Platform profiles** — Add UI filtering profiles for new video platforms (see below)
- **Bug reports** — Found something broken? Open an issue with steps to reproduce
- **Feature requests** — Ideas for improving the pipeline? Open an issue to discuss
- **Code** — Pick up an open issue or propose a change via PR
- **Documentation** — Improvements to docs, examples, or README
- **Testing** — More test cases, edge cases, sample fixtures

## Development Setup

### Prerequisites

- Python 3.11+
- NVIDIA GPU with CUDA (optional but recommended)
- ffmpeg installed and on PATH
- [uv](https://docs.astral.sh/uv/) package manager

### Getting Started

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/omniscribe.git
cd omniscribe

# Create virtual environment and install dependencies
uv sync --all-extras

# Copy environment config
cp .env.example .env
# Edit .env with your settings (GPU device, language preferences, etc.)

# Run tests
uv run pytest

# Run linting
uv run ruff check .
uv run mypy src/
```

### Project Structure

See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) for a full overview of the architecture and project structure.

## Code Style

- **Formatter/Linter:** [Ruff](https://docs.astral.sh/ruff/) — configured in `pyproject.toml`
- **Type checking:** [mypy](https://mypy-lang.org/) in strict mode
- **Docstrings:** Google style
- **Naming:** snake_case for functions/variables, PascalCase for classes
- **Commits:** Use clear, descriptive commit messages. Prefix with the module area when useful (e.g. `ocr: add scene change detection`, `asr: fix language auto-detection`)

## Pull Request Process

1. **Fork the repo** and create a feature branch from `main`
2. **Keep PRs focused** — one feature or fix per PR
3. **Add tests** for new functionality
4. **Run the full test suite** before submitting: `uv run pytest`
5. **Run linting**: `uv run ruff check . && uv run mypy src/`
6. **Update documentation** if your change affects usage or configuration
7. **Describe your changes** clearly in the PR description

## Adding a Platform Profile

One of the easiest ways to contribute is adding support for a new video platform. Platform profiles define where UI elements appear on screen so the OCR engine can ignore them.

### Steps

1. Create a new file in `src/omniscribe/platforms/` (e.g. `twitter.py`)
2. Define the profile by extending `BasePlatformProfile`:
   - **UI exclusion zones** — Relative screen regions where the platform's UI lives (e.g. like buttons, navigation bars)
   - **Text patterns** — Regex patterns for UI text to filter (usernames, counts, attribution)
   - **URL patterns** — How to detect this platform from a URL
3. Register the profile in `src/omniscribe/platforms/__init__.py`
4. Add tests in `tests/test_platforms.py`
5. Ideally include a few sample screenshots (with UI regions annotated) in your PR description

Look at `src/omniscribe/platforms/tiktok.py` as a reference implementation.

## Reporting Bugs

When opening a bug report, please include:

- OS and Python version
- GPU model and CUDA version (if applicable)
- OmniScribe version
- The command you ran
- Full error output / traceback
- The video URL or a description of the video type (if relevant)

## Questions?

Open a [Discussion](https://github.com/dagonet/omniscribe/discussions) on GitHub — issues are for bugs and concrete feature requests.
