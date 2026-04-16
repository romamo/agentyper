"""
agentyper — Agent-first Python CLI library.

Typer-compatible API built on argparse + pydantic.

Quick start (single function)::

    import agentyper

    def search(ticker: str, limit: int = 10):
        \"\"\"Search securities by ticker.\"\"\"
        results = service.search(ticker, limit)
        agentyper.output(results)

    agentyper.run(search)

Multi-command app::

    import agentyper

    app = agentyper.Agentyper(name="my-tool", version="1.0.0")

    @app.command()
    def search(ticker: str, limit: int = agentyper.Option(10, help="Max results")):
        \"\"\"Search securities.\"\"\"
        results = service.search(ticker, limit)
        agentyper.output(results)

    app()

Typer migration (one line change)::

    # import typer          ← before
    import agentyper as typer  # ← after

"""

from __future__ import annotations

__version__ = "0.1.11"

# Core app
from agentyper._internal._app import (
    Abort,
    Agentyper,
    BadParameter,
    Context,
    Exit,
    get_current_context,
    run,
)

# Errors
from agentyper._internal._errors import (
    EXIT_CODE_TABLE,
    EXIT_SUCCESS,
    EXIT_SYSTEM,
    EXIT_VALIDATION,
    ExitCode,
    ExitCodeEntry,
    exit_error,
    format_pydantic_error,
)

# Interactive
from agentyper._internal._interactive import (
    confirm,
    edit,
    launch,
    pager,
    progressbar,
    prompt,
)

# Output
from agentyper._internal._output import (
    add_warning,
    echo,
    external_data,
    output,
    render_output,
    result,
    set_pagination,
    warn_truncated,
)

# Parameters
from agentyper._internal._params import (
    Argument,
    ArgumentInfo,
    Option,
    OptionInfo,
    ResourceId,
)

# Schema (public API)
from agentyper._internal._schema import (
    build_app_schema,
    fn_to_input_schema,
)

__all__ = [
    # App
    "Agentyper",
    "run",
    # Params
    "Option",
    "Argument",
    "OptionInfo",
    "ArgumentInfo",
    "ResourceId",
    # Output
    "add_warning",
    "echo",
    "external_data",
    "output",
    "render_output",
    "result",
    "set_pagination",
    "warn_truncated",
    # Interactive
    "confirm",
    "prompt",
    "edit",
    "progressbar",
    "pager",
    "launch",
    # Errors
    "ExitCode",
    "ExitCodeEntry",
    "EXIT_CODE_TABLE",
    "exit_error",
    "format_pydantic_error",
    "EXIT_SUCCESS",
    "EXIT_VALIDATION",
    "EXIT_SYSTEM",
    # Schema
    "build_app_schema",
    "fn_to_input_schema",
    # Compat
    "Exit",
    "Abort",
    "BadParameter",
    "Context",
    "get_current_context",
    # Version
    "__version__",
]
