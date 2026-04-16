"""Agentyper CLI."""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

import agentyper

app = agentyper.Agentyper(
    name="agentyper",
    help="Run Agentyper scripts with completion, without having to create a package.",
)


def _load_module(path: str) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        agentyper.echo(f"Error: Path '{path}' does not exist.", err=True)
        sys.exit(1)

    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        agentyper.echo(f"Error: Could not load module from '{path}'.", err=True)
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _execute_module(module: Any, target_args: list[str], module_name: str) -> None:
    # 1. Look for Agentyper instance
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        attr = getattr(module, attr_name)
        if isinstance(attr, agentyper.Agentyper):
            attr(target_args)
            return

    # 2. Extract functions
    funcs = []
    if hasattr(module, "main") and inspect.isfunction(module.main):
        funcs.append(module.main)
    else:
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            attr = getattr(module, attr_name)
            if inspect.isfunction(attr) and attr.__module__ == module.__name__:
                funcs.append(attr)

    if not funcs:
        agentyper.echo("Error: Could not find a valid Agentyper app or function to run.", err=True)
        sys.exit(1)

    if len(funcs) == 1:
        # Run as a single-command app
        agentyper.run(funcs[0], args=target_args, prog=module_name)
        return

    # Run as a multi-command app
    target_app = agentyper.Agentyper(name=module_name)
    for f in funcs:
        target_app.command(name=f.__name__.replace("_", "-"))(f)

    target_app(target_args)


@app.callback(invoke_without_command=True)
def callback(
    path_or_module: str | None = agentyper.Argument(
        None, metavar="[PATH_OR_MODULE]", help="Path to Python script or module to run."
    ),
) -> None:
    """Run Agentyper scripts with completion, without having to create a package."""
    pass


@app.command(option_placement="strict")
def run() -> None:
    """Run the provided Agentyper app."""
    # This command handles parsing `run` in help, but execution is intercepted below.
    agentyper.echo(
        "Error: Please provide a script to run. Usage: agentyper [PATH_OR_MODULE] run ...",
        err=True,
    )
    sys.exit(1)


def main() -> None:
    """CLI entry point."""
    # Intercept `agentyper PATH_OR_MODULE run [ARGS...]` pattern
    if len(sys.argv) >= 3 and sys.argv[2] == "run":
        path_or_module = sys.argv[1]
        target_args = sys.argv[3:]

        # Override sys.argv to hide the wrapper from the target script
        sys.argv = [path_or_module] + target_args

        module = _load_module(path_or_module)
        _execute_module(module, target_args, module_name=Path(path_or_module).stem)
        return

    # Fallback to standard app (renders help)
    app()


if __name__ == "__main__":
    main()
