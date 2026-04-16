# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.10] - 2026-04-16

### Changed
- **Context guidance docs**: clarified when CLI authors should pass `ctx` explicitly versus calling `agentyper.get_current_context()` in deep helpers.
- **Developer examples**: documented the main `agentyper.Context` fields and added a hybrid helper pattern that is convenient for reuse and testing.

## [0.1.9] - 2026-04-16

### Added
- **Invocation context API**: commands and callbacks now share a richer `agentyper.Context`, and helper code can access the active invocation via `agentyper.get_current_context()`.

### Fixed
- **Root invocation safety**: callback-only invocations no longer risk dereferencing command timeout metadata when no subcommand is selected.
- **Test/lint cleanup**: replaced a bare `assert False` path in the test suite and normalized import ordering to keep Ruff clean.

## [0.1.8] - 2026-04-07

### Added
- **Variadic positional arguments**: `list[T]` parameter annotations now map to `nargs="*"` / `nargs="+"` argparse positionals, enabling commands that accept multiple files or IDs.

## [0.1.7] - 2026-04-05

### Added
- **Idempotency support** (`--idempotency-key`): mutating/destructive commands deduplicate calls within a session (REQ-C-007).
- **Per-invocation timeout** (`--timeout`): SIGALRM-based wall-clock timeout with structured JSON cancellation response (REQ-F-011).
- **SIGTERM handler**: emits partial JSON cancellation envelope and exits 143 on SIGTERM (REQ-F-013).
- **SIGPIPE handler**: exits cleanly when the consumer closes the pipe (REQ-F-014).
- **CI auto-format**: `--format json` is applied automatically when `CI=true` or `NO_COLOR` is set (REQ-F-003).
- **Help to stderr in non-TTY** (`_HelpAction`): `--help` output is routed to stderr when stdout is not a TTY (REQ-F-048).
- **ResourceId type**: parameter annotation that auto-validates against agent hallucination patterns (path traversal, shell metacharacters, percent-encoding, etc.) (REQ-F-044/045).
- **Structured exit codes**: `ExitCode`, `ExitCodeEntry`, `EXIT_CODE_TABLE` are now public API; per-command exit code declarations via `exit_codes=` on `@app.command()`.
- **New output helpers**: `result()`, `add_warning()`, `warn_truncated()`, `external_data()`, `set_pagination()` added to public API.
- **Contacts example app**: full CRUD contacts CLI under `examples/contacts/` demonstrating ResourceId, mutating commands, and idempotency.
- **Agent and developer docs**: `docs/for-agents.md` and `docs/for-developers.md` added.
- **CI workflow**: GitHub Actions `ci.yml` for lint, test, and build on every push.

### Fixed
- **Code quality**: replaced bare `try/except/pass` with `contextlib.suppress`; fixed import ordering; resolved all ruff warnings across the codebase.

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
