# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.6] - 2026-03-19

### Fixed
- **Code Style**: Minor formatting fixes for better PEP 8 compliance.

## [0.1.5] - 2026-03-19

### Fixed
- **JSON Output**: Unicode characters (e.g. Cyrillic) are now rendered correctly instead of being escaped (set `ensure_ascii=False`).

## [0.1.4] - 2026-03-16
(Existing 0.1.4 changes)

## [0.1.0] - 2026-03-03

### Added
- **CLI Tool (`agentyper`)**: Run Python scripts directly without importing `agentyper`, automatically exposing functions as commands (`agentyper main.py run`).
- **Core App Framework**: `Agentyper`, `@app.command()`, `@app.callback()`, `app()`, `app.add_agentyper()`, `app.add_typer()`.
- **Global Flags Auto-injection**:
  - `--format {table,json,csv}` with automatic terminal detection (`isatty()`).
  - `--schema` to auto-export JSON Schema of a specific command or the full application.
  - `--yes` and `--no` bypassing for confirmations.
  - `--answers` JSON payload to bypass all interactive prompts (prompts, edits, confirms) non-blockingly.
  - `-v` and `-vv` for built-in INFO/DEBUG level logging.
- **Output Interfaces**: `agentyper.output()`, `agentyper.echo()`, structured JSON `agentyper.exit_error()`.
- **Interactive Interfaces (with bypass)**: `confirm()`, `prompt()`, `edit()`, `progressbar()`, `pager()`, `launch()`.
- **Agent Ergonomics**:
  - Structured field-level validation errors automatically captured via Pydantic.
  - Exit code taxonomy (`0` success, `1` validation retry, `2` system failure).
  - Schema includes description and metavars derived directly from Python docstrings.
  - Integration with environment variables `envvar` and context objects.
- **Typer API Compatibility**: Designed as a drop-in replacement (`import agentyper as typer`). Includes aliases for `Exit`, `Abort`, `BadParameter`, `Context`.
