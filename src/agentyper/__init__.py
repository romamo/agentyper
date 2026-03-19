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

__version__ = "0.1.6"

# Core app
from agentyper._internal._app import (
    Abort,
    Agentyper,
    BadParameter,
    Context,
    Exit,
    run,
)

# Errors
from agentyper._internal._errors import (
    EXIT_SUCCESS,
    EXIT_SYSTEM,
    EXIT_VALIDATION,
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
    echo,
    output,
    render_output,
)

# Parameters
from agentyper._internal._params import (
    Argument,
    ArgumentInfo,
    Option,
    OptionInfo,
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
    # Output
    "echo",
    "output",
    "render_output",
    # Interactive
    "confirm",
    "prompt",
    "edit",
    "progressbar",
    "pager",
    "launch",
    # Errors
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
    # Version
    "__version__",
]
