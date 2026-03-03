"""
Interactive feature implementations with full resolution hierarchy.

Every function is safe to call in agent (non-TTY) context:
- confirm()     → --yes / --no / --answers queue / TTY / error
- prompt()      → --answers dict/queue / default / TTY / error
- edit()        → --answers["edit"] / piped stdin / $EDITOR / error
- progressbar() → Rich display (TTY) or silent passthrough (non-TTY)
- pager()       → Rich pager (TTY) or direct stream (non-TTY)
- launch()      → webbrowser (TTY) or JSON stderr notice (non-TTY)
"""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import webbrowser
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from rich.console import Console as _RichConsole
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from agentyper._internal._errors import EXIT_VALIDATION, exit_error
from agentyper._internal._output import echo
from agentyper._internal._session import get_session

T = TypeVar("T")

_MISSING = object()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ---------------------------------------------------------------------------
# confirm()
# ---------------------------------------------------------------------------


def confirm(text: str, default: bool = False) -> bool:
    """
    Ask the user for a yes/no confirmation.

    In agent/non-TTY context, resolves through the session's bypass hierarchy.
    Interactive only when running in a terminal AND no bypass is configured.

    Args:
        text:    The question to display.
        default: Default value if no answer is supplied.

    Returns:
        ``True`` if confirmed, ``False`` if denied.
    """
    session = get_session()
    resolved = session.resolve_confirm(text, default)
    if resolved is not None:
        return resolved

    if sys.stdin.isatty():
        return _ask_yn(text, default)

    # Non-TTY and no bypass — error with actionable hint
    exit_error(
        f"confirm('{text[:50]}') requires TTY or --yes/--no/--answers flag.",
        code=EXIT_VALIDATION,
        error_type="InteractiveRequired",
        format_="json" if not sys.stderr.isatty() else "table",
    )


def _ask_yn(text: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{text} {hint}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'.")


# ---------------------------------------------------------------------------
# prompt()
# ---------------------------------------------------------------------------


def prompt(
    text: str,
    default: Any = None,
    type_: type = str,
    param_name: str | None = None,
    *,
    hide_input: bool = False,
    confirmation_prompt: bool = False,
) -> Any:
    """
    Prompt the user for a value.

    In agent/non-TTY context, resolves from ``--answers`` or default.

    Args:
        text:                Prompt text shown to the human (also used as dict key).
        default:             Default value (used in agent mode if no answer supplied).
        type_:               Type coercion applied to the raw string input.
        param_name:          Explicit dict key for ``--answers`` lookup.
                             Defaults to slugified version of ``text``.
        hide_input:          If True, masks the typed input (e.g. for passwords).
        confirmation_prompt: If True, asks the user to repeat their input to confirm.

    Returns:
        The resolved value, coerced to ``type_``.
    """
    key = param_name or _slugify(text)
    session = get_session()
    resolved = session.resolve_prompt(key, default)

    if resolved is not None:
        try:
            return type_(resolved) if not isinstance(resolved, type_) else resolved
        except (ValueError, TypeError) as e:
            exit_error(f"Invalid answer for '{key}': {e}", code=EXIT_VALIDATION)

    if sys.stdin.isatty():
        while True:
            hint = f" [{default}]" if default is not None and not hide_input else ""
            prompt_str = f"{text}{hint}: "

            raw = getpass.getpass(prompt_str) if hide_input else input(prompt_str)
            raw = raw.strip()
            if not raw and default is not None:
                raw = str(default)

            if confirmation_prompt:
                raw2 = (
                    getpass.getpass("Repeat for confirmation: ")
                    if hide_input
                    else input("Repeat for confirmation: ")
                ).strip()

                if raw != raw2:
                    echo("Error: the two entered values do not match.", err=True)
                    continue

            try:
                return type_(raw)
            except (ValueError, TypeError) as e:
                exit_error(f"Invalid input for '{key}': {e}", code=EXIT_VALIDATION)

    if default is not None:
        return default

    exit_error(
        f"prompt('{text[:50]}') requires TTY or --answers flag.",
        code=EXIT_VALIDATION,
        error_type="InteractiveRequired",
        format_="json" if not sys.stderr.isatty() else "table",
    )


# ---------------------------------------------------------------------------
# edit()
# ---------------------------------------------------------------------------


def edit(text: str = "", extension: str = ".txt") -> str:
    """
    Open a text editor for the user to edit content.

    In agent/non-TTY context, resolves from ``--answers["edit"]`` or piped STDIN.

    Args:
        text:      Initial content pre-filled in the editor.
        extension: File extension for the temporary file (affects syntax highlighting).

    Returns:
        The edited content as a string.
    """
    session = get_session()
    resolved = session.resolve_edit(text)
    if resolved is not None:
        return resolved

    # Piped stdin (not TTY) — read as replacement content
    if not sys.stdin.isatty() and not sys.stdin.closed:
        return sys.stdin.read()

    if sys.stdin.isatty():
        return _open_editor(text, extension)

    exit_error(
        "edit() requires a terminal editor or pre-supplied content via --answers.",
        code=EXIT_VALIDATION,
        error_type="InteractiveRequired",
    )


def _open_editor(text: str, extension: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(suffix=extension, mode="w", delete=False) as f:
        f.write(text)
        fname = f.name
    subprocess.call([editor, fname])  # noqa: S603
    with open(fname) as f:  # noqa: PTH123
        return f.read()


# ---------------------------------------------------------------------------
# progressbar()
# ---------------------------------------------------------------------------


@contextmanager
def progressbar(
    iterable: Iterable[T],
    label: str = "",
    length: int | None = None,
) -> Generator[Iterator[T], None, None]:
    """
    Display a progress bar while iterating.

    In agent/non-TTY context, yields the iterable as-is (silent passthrough).

    Args:
        iterable: The sequence to iterate over.
        label:    Label shown next to the progress bar.
        length:   Total item count (needed for generators without ``__len__``).

    Yields:
        The same iterable, optionally wrapped with progress tracking.
    """
    if not sys.stdout.isatty():
        # Silent passthrough — agent gets the data, no TTY noise
        yield iter(iterable)  # type: ignore[arg-type]
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=True,
    ) as progress:
        task = progress.add_task(label or "Processing", total=length)

        def _track() -> Iterator[T]:
            for item in iterable:
                progress.advance(task)
                yield item

        yield _track()


# ---------------------------------------------------------------------------
# pager()
# ---------------------------------------------------------------------------


@contextmanager
def pager() -> Generator[None, None, None]:
    """
    Page long output through a terminal pager.

    In agent/non-TTY context, content streams directly to stdout.
    """
    if not sys.stdout.isatty():
        yield
        return

    with _RichConsole().pager(styles=True):
        yield


# ---------------------------------------------------------------------------
# launch()
# ---------------------------------------------------------------------------


def launch(url: str, locate: bool = False) -> None:
    """
    Open a URL in the system's default browser.

    In agent/non-TTY context, emits a JSON notice to stderr instead of
    opening the browser, so the agent can handle the URL programmatically.

    Args:
        url:    The URL to open.
        locate: If True, open the file manager at the path (passed to webbrowser).
    """
    if not sys.stdout.isatty():
        notice = json.dumps({"side_effect": "open_url", "url": url, "locate": locate})
        print(notice, file=sys.stderr)
        return

    webbrowser.open(url, new=2)
