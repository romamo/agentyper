"""Testing utilities for agentyper CLIs — mirrors typer.testing.CliRunner."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

import agentyper._internal._errors as _err
import agentyper._internal._output as _out
from agentyper._internal._app import Agentyper, run


@dataclass
class Result:
    """Captured result of a CLI invocation."""

    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None = None

    @property
    def output(self) -> str:
        """Alias for stdout — mirrors typer.testing.Result."""
        return self.stdout

    def __repr__(self) -> str:
        return (
            f"Result(exit_code={self.exit_code}, "
            f"stdout={self.stdout!r:.80}, "
            f"stderr={self.stderr!r:.40})"
        )


class CliRunner:
    """
    Invoke an :class:`~agentyper.Agentyper` app or a plain function with
    ``agentyper.run()`` semantics in a subprocess-free test environment.

    Captures stdout, stderr, and the exit code.

    Example::

        runner = CliRunner()
        result = runner.invoke(app, ["search", "AAPL", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
    """

    def invoke(
        self,
        app_or_fn: Agentyper | Callable,
        args: list[str] | None = None,
        *,
        catch_exceptions: bool = True,
    ) -> Result:
        """
        Run the app with the given arguments, capturing all output.

        Args:
            app_or_fn:        An :class:`~agentyper.Agentyper` instance or a plain
                              function (invoked via :func:`~agentyper.run`).
            args:             Command-line arguments (excluding ``sys.argv[0]``).
            catch_exceptions: If ``True`` (default), catch all exceptions and
                              return them in :attr:`Result.exception`. If ``False``,
                              re-raise after capture.

        Returns:
            A :class:`Result` instance.
        """
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        exit_code = 0
        exc: BaseException | None = None

        old_stdout = sys.stdout
        old_stderr = sys.stderr

        # Rich Console caches the file handle — patch it directly too
        old_console = _out._console
        old_err_console = _err._err_console
        new_console = Console(file=stdout_buf, highlight=False, markup=False)
        new_err_console = Console(file=stderr_buf, stderr=True, highlight=False, markup=False)

        try:
            sys.stdout = stdout_buf  # type: ignore[assignment]
            sys.stderr = stderr_buf  # type: ignore[assignment]
            _out._console = new_console
            _err._err_console = new_err_console

            if isinstance(app_or_fn, Agentyper):
                app_or_fn(args or [])
            else:
                run(app_or_fn, args or [])

        except SystemExit as e:
            exit_code = int(e.code) if e.code is not None else 0
        except BaseException as e:  # noqa: BLE001
            exit_code = 1
            exc = e
            if not catch_exceptions:
                raise
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            _out._console = old_console
            _err._err_console = old_err_console

        return Result(
            exit_code=exit_code,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            exception=exc,
        )
