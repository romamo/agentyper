"""Smoke tests for agentyper library."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import agentyper
from agentyper.testing import CliRunner, Result

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_search_app() -> agentyper.Agentyper:
    app = agentyper.Agentyper(name="test-tool", version="0.1.0")

    @app.command()
    def search(ticker: str, limit: int = agentyper.Option(10, "--limit", "-l", help="Max results")):
        """Search securities by ticker."""
        results = [{"ticker": ticker, "price": 178.50}] * min(limit, 3)
        agentyper.output(results)

    return app


def make_fn() -> None:
    """A standalone function for agentyper.run() testing."""
    pass


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchema:
    def test_app_schema_contains_version(self) -> None:
        app = make_search_app()
        schema = app.get_schema()
        assert schema["version"] == "0.1.0"

    def test_app_schema_contains_commands(self) -> None:
        app = make_search_app()
        schema = app.get_schema()
        assert "search" in schema["commands"]

    def test_command_schema_has_input(self) -> None:
        app = make_search_app()
        schema = app.get_schema()
        cmd = schema["commands"]["search"]
        assert "input_schema" in cmd
        assert "ticker" in cmd["input_schema"]["properties"]
        assert cmd["input_schema"]["properties"]["ticker"]["type"] == "string"

    def test_schema_flag_exits_zero(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["--schema"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "commands" in data

    def test_command_schema_flag(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search", "--schema"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["type"] == "object"
        assert "ticker" in data["properties"]

    def test_fn_schema_via_run(self) -> None:
        def greet(name: str, count: int = 1):
            """Greet a user."""
            agentyper.echo(f"Hello {name}!")

        result = runner.invoke(greet, ["--schema"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "name" in data["properties"]
        assert "count" in data["properties"]


# ---------------------------------------------------------------------------
# Output format tests
# ---------------------------------------------------------------------------


class TestOutputFormat:
    def test_json_format(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search", "AAPL", "--format", "json"])
        assert result.exit_code == 0
        envelope = json.loads(result.stdout)
        assert envelope["ok"] is True
        assert envelope["error"] is None
        assert isinstance(envelope["warnings"], list)
        assert "duration_ms" in envelope["meta"]
        data = envelope["data"]
        assert isinstance(data, list)
        assert data[0]["ticker"] == "AAPL"

    def test_csv_format(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search", "AAPL", "--format", "csv"])
        assert result.exit_code == 0
        lines = result.stdout.strip().splitlines()
        assert lines[0] == "ticker,price"
        assert "AAPL" in lines[1]

    def test_table_format_default(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search", "AAPL", "--format", "table"])
        assert result.exit_code == 0
        assert "AAPL" in result.stdout


# ---------------------------------------------------------------------------
# Exit code tests
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_missing_required_arg_exits_nonzero(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search"])
        assert result.exit_code != 0

    def test_success_exits_zero(self) -> None:
        app = make_search_app()
        result = runner.invoke(app, ["search", "AAPL", "--format", "json"])
        assert result.exit_code == 0

    def test_exit_error_validation(self) -> None:
        app = agentyper.Agentyper(name="t")

        @app.command()
        def bad() -> None:
            """Always fails validation."""
            agentyper.exit_error("bad input", code=agentyper.EXIT_VALIDATION)

        result = runner.invoke(app, ["bad"])
        assert result.exit_code == agentyper.EXIT_VALIDATION

    def test_exit_error_system(self) -> None:
        app = agentyper.Agentyper(name="t")

        @app.command()
        def fail() -> None:
            """System failure."""
            agentyper.exit_error("db down", code=agentyper.EXIT_SYSTEM)

        result = runner.invoke(app, ["fail"])
        assert result.exit_code == agentyper.EXIT_SYSTEM


# ---------------------------------------------------------------------------
# Interactive resolution tests
# ---------------------------------------------------------------------------


class TestInteractiveResolution:
    def test_confirm_with_yes_flag(self) -> None:
        app = agentyper.Agentyper(name="t")
        confirmed = []

        @app.command()
        def delete(name: str) -> None:
            """Delete."""
            confirmed.append(agentyper.confirm(f"Delete {name}?"))
            agentyper.echo("done")

        result = runner.invoke(app, ["delete", "alice", "--yes"])
        assert result.exit_code == 0
        assert confirmed == [True]

    def test_confirm_with_no_flag(self) -> None:
        app = agentyper.Agentyper(name="t")
        confirmed = []

        @app.command()
        def delete(name: str) -> None:
            """Delete."""
            confirmed.append(agentyper.confirm(f"Delete {name}?"))

        runner.invoke(app, ["delete", "alice", "--no"])
        assert confirmed == [False]

    def test_confirm_via_answers_queue(self) -> None:
        app = agentyper.Agentyper(name="t")
        confirmed = []

        @app.command()
        def multi() -> None:
            """Multiple confirms."""
            confirmed.append(agentyper.confirm("First?"))
            confirmed.append(agentyper.confirm("Second?"))

        answers = json.dumps({"confirms": [True, False]})
        runner.invoke(app, ["multi", "--answers", answers])
        assert confirmed == [True, False]

    def test_prompt_via_answers_dict(self) -> None:
        app = agentyper.Agentyper(name="t")
        collected = []

        @app.command()
        def wizard() -> None:
            """Wizard."""
            collected.append(agentyper.prompt("Enter name"))

        answers = json.dumps({"prompts": {"enter_name": "Alice"}})
        runner.invoke(app, ["wizard", "--answers", answers])
        assert collected == ["Alice"]

    def test_prompt_via_answers_queue(self) -> None:
        app = agentyper.Agentyper(name="t")
        collected = []

        @app.command()
        def wizard() -> None:
            """Wizard."""
            collected.append(agentyper.prompt("Enter name"))
            collected.append(agentyper.prompt("Enter role"))

        answers = json.dumps({"prompts": ["Alice", "admin"]})
        runner.invoke(app, ["wizard", "--answers", answers])
        assert collected == ["Alice", "admin"]


# ---------------------------------------------------------------------------
# agentyper.run() tests
# ---------------------------------------------------------------------------


class Testrun:
    def test_run_basic(self) -> None:
        outputs = []

        def greet(name: str):
            """Greet."""
            outputs.append(name)

        result = runner.invoke(greet, ["Alice"])
        assert result.exit_code == 0
        assert outputs == ["Alice"]

    def test_run_schema(self) -> None:
        def greet(name: str, count: int = 1):
            """Greet a user."""

        result = runner.invoke(greet, ["--schema"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "name" in data["properties"]

    def test_run_help_keeps_timeout_flag_by_default(self) -> None:
        def greet(name: str) -> None:
            """Greet."""

        result = runner.invoke(greet, ["--help"])
        help_text = result.stdout + result.stderr
        assert result.exit_code == 0
        assert "--timeout" in help_text


# ---------------------------------------------------------------------------
# Sub-app tests
# ---------------------------------------------------------------------------


class TestSubApps:
    def test_subapp_dispatch(self) -> None:
        app = agentyper.Agentyper(name="root")
        sub = agentyper.Agentyper(name="tx")
        results = []

        @sub.command()
        def add(amount: float) -> None:
            """Add tx."""
            results.append(amount)

        app.add_agentyper(sub, name="tx")
        runner.invoke(app, ["tx", "add", "10.5"])
        assert results == [10.5]

    def test_subapp_schema(self) -> None:
        app = agentyper.Agentyper(name="root", version="1.0")
        sub = agentyper.Agentyper(name="tx")

        @sub.command()
        def add(amount: float) -> None:
            """Add tx."""

        app.add_agentyper(sub, name="tx")
        schema = app.get_schema()
        assert "tx" in schema["commands"]


# ---------------------------------------------------------------------------
# Advanced features tests
# ---------------------------------------------------------------------------


class TestAdvancedFeatures:
    def test_envvar_option(self, monkeypatch) -> None:
        app = agentyper.Agentyper(name="app")
        val = None

        @app.command()
        def cmd(api_key: str = agentyper.Option(..., envvar="TEST_API_KEY")):
            nonlocal val
            val = api_key

        # Set env var and invoke without argument
        monkeypatch.setenv("TEST_API_KEY", "secret123")
        res = runner.invoke(app, ["cmd"])
        assert res.exit_code == 0
        assert val == "secret123"

    def test_context_injection(self) -> None:
        app = agentyper.Agentyper(name="app")
        contexts = []

        @app.callback()
        def cb(ctx: agentyper.Context):
            contexts.append("callback")
            ctx.obj["shared"] = 42

        @app.command()
        def cmd(ctx: agentyper.Context):
            contexts.append(f"cmd-{ctx.obj.get('shared')}")

        res = runner.invoke(app, ["cmd"])
        assert res.exit_code == 0
        assert contexts == ["callback", "cmd-42"]

    def test_context_exposes_resolved_invocation_state(self) -> None:
        app = agentyper.Agentyper(name="app", interactive=True, default_timeout_ms=500)
        captured: dict[str, object] = {}

        @app.command()
        def cmd(ctx: agentyper.Context) -> None:
            captured["format"] = ctx.format
            captured["format_compat"] = ctx.format_
            captured["verbose"] = ctx.verbose
            captured["yes"] = ctx.yes
            captured["no"] = ctx.no
            captured["answers"] = ctx.answers
            captured["timeout_ms"] = ctx.timeout_ms
            captured["runtime_timeout_ms"] = ctx.runtime.timeout_ms
            captured["runtime_verbosity"] = ctx.runtime.verbosity
            captured["root_timeout_ms"] = ctx.root.timeout_ms
            captured["root_verbose"] = ctx.globals.verbose

        answers = json.dumps({"prompts": {"name": "Alice"}})
        res = runner.invoke(app, ["cmd", "--format", "json", "-vv", "--yes", "--answers", answers])
        assert res.exit_code == 0
        assert captured == {
            "format": "json",
            "format_compat": "json",
            "verbose": 2,
            "yes": True,
            "no": False,
            "answers": answers,
            "timeout_ms": 500,
            "runtime_timeout_ms": 500,
            "runtime_verbosity": 2,
            "root_timeout_ms": 500,
            "root_verbose": 2,
        }

    def test_help_hides_interaction_and_timeout_flags_when_unused(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd(name: str) -> None:
            """Simple command."""

        res = runner.invoke(app, ["cmd", "--help"])
        help_text = res.stdout + res.stderr
        assert res.exit_code == 0
        assert "--yes" not in help_text
        assert "--no" not in help_text
        assert "--answers" not in help_text
        assert "--timeout" not in help_text

    def test_hidden_interaction_flags_are_still_accepted_and_reach_context(self) -> None:
        app = agentyper.Agentyper(name="app")
        captured: dict[str, object] = {}

        @app.command()
        def cmd(ctx: agentyper.Context) -> None:
            captured["yes"] = ctx.yes
            captured["no"] = ctx.no
            captured["answers"] = ctx.answers

        answers = json.dumps({"confirms": [True]})
        res = runner.invoke(app, ["cmd", "--yes", "--answers", answers])
        assert res.exit_code == 0
        assert captured == {
            "yes": True,
            "no": False,
            "answers": answers,
        }

    def test_interaction_flags_are_auto_added_when_command_uses_prompt_routines(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd() -> None:
            """Interactive command."""
            agentyper.confirm("Continue?")

        help_res = runner.invoke(app, ["cmd", "--help"])
        help_text = help_res.stdout + help_res.stderr
        assert help_res.exit_code == 0
        assert "--yes" in help_text
        assert "--answers" in help_text

        res = runner.invoke(app, ["cmd", "--yes"])
        assert res.exit_code == 0

    def test_interaction_flags_can_be_explicitly_enabled(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command(interactive=True)
        def cmd() -> None:
            """Declared interactive."""

        help_res = runner.invoke(app, ["cmd", "--help"])
        help_text = help_res.stdout + help_res.stderr
        assert help_res.exit_code == 0
        assert "--yes" in help_text
        assert "--answers" in help_text

    def test_timeout_flag_is_hidden_until_enabled(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd() -> None:
            """No timeout."""

        res = runner.invoke(app, ["cmd", "--help"])
        help_text = res.stdout + res.stderr
        assert res.exit_code == 0
        assert "--timeout" not in help_text

    def test_timeout_flag_is_added_when_command_declares_timeout(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command(timeout_ms=250)
        def cmd() -> None:
            """Timed command."""

        res = runner.invoke(app, ["cmd", "--help"])
        help_text = res.stdout + res.stderr
        assert res.exit_code == 0
        assert "--timeout" in help_text

    def test_callback_option_before_subcommand_is_parsed(self) -> None:
        app = agentyper.Agentyper(name="app")
        seen: list[bool] = []

        @app.callback()
        def cb(enabled: bool = agentyper.Option(False, "--enabled", is_flag=True)) -> None:
            seen.append(enabled)

        @app.command()
        def cmd() -> None:
            pass

        res = runner.invoke(app, ["--enabled", "cmd"])
        assert res.exit_code == 0
        assert seen == [True]

    def test_callback_option_after_subcommand_is_parsed(self) -> None:
        app = agentyper.Agentyper(name="app")
        seen: list[bool] = []

        @app.callback()
        def cb(enabled: bool = agentyper.Option(False, "--enabled", is_flag=True)) -> None:
            seen.append(enabled)

        @app.command()
        def cmd() -> None:
            pass

        res = runner.invoke(app, ["cmd", "--enabled"])
        assert res.exit_code == 0
        assert seen == [True]

    def test_global_format_before_subcommand_survives_subparser_defaults(self) -> None:
        app = agentyper.Agentyper(name="app")
        seen: dict[str, str] = {}

        @app.callback()
        def cb(ctx: agentyper.Context) -> None:
            seen["callback"] = ctx.format_

        @app.command()
        def cmd(ctx: agentyper.Context) -> None:
            seen["command"] = ctx.format_

        res = runner.invoke(app, ["--format", "table", "cmd"])
        assert res.exit_code == 0
        assert seen == {"callback": "table", "command": "table"}

    def test_get_current_context_is_available_to_helpers(self) -> None:
        app = agentyper.Agentyper(name="app")
        seen: dict[str, object] = {}

        def helper() -> agentyper.Context:
            return agentyper.get_current_context()

        @app.command()
        def cmd(ctx: agentyper.Context, name: str) -> None:
            helper_ctx = helper()
            seen["same_object"] = helper_ctx is ctx
            seen["command_name"] = helper_ctx.command_name
            seen["app_name"] = helper_ctx.app_name
            seen["param_name"] = helper_ctx.params.name

        res = runner.invoke(app, ["cmd", "Alice"])
        assert res.exit_code == 0
        assert seen == {
            "same_object": True,
            "command_name": "cmd",
            "app_name": "app",
            "param_name": "Alice",
        }

    def test_get_current_context_resets_after_invocation(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd() -> None:
            agentyper.get_current_context()

        res = runner.invoke(app, ["cmd"])
        assert res.exit_code == 0

        try:
            agentyper.get_current_context()
        except RuntimeError as exc:
            assert str(exc) == "No active agentyper invocation context"
        else:
            raise AssertionError("expected get_current_context() to fail outside invocation")

    def test_invoke_without_command_runs_callback_context(self) -> None:
        app = agentyper.Agentyper(name="app", invoke_without_command=True, default_timeout_ms=250)
        seen: dict[str, object] = {}

        @app.callback()
        def cb(ctx: agentyper.Context) -> None:
            current = agentyper.get_current_context()
            seen["same_object"] = current is ctx
            seen["command_name"] = current.command_name
            seen["timeout_ms"] = current.timeout_ms

        res = runner.invoke(app, [])
        assert res.exit_code == 0
        assert seen == {
            "same_object": True,
            "command_name": None,
            "timeout_ms": 250,
        }

    def test_subapp_callback(self) -> None:
        app = agentyper.Agentyper(name="root")
        sub = agentyper.Agentyper(name="sub")
        calls = []

        @app.callback()
        def cb1():
            calls.append("root")

        @sub.callback()
        def cb2():
            calls.append("sub")

        @sub.command()
        def cmd():
            calls.append("cmd")

        app.add_agentyper(sub, name="sub")
        res = runner.invoke(app, ["sub", "cmd"])
        assert res.exit_code == 0
        assert calls == ["root", "sub", "cmd"]

    def test_list_parameter(self) -> None:
        app = agentyper.Agentyper(name="app")
        received = []

        @app.command()
        def cmd(tags: list[str]) -> None:
            """Accept a list."""
            received.extend(tags)

        res = runner.invoke(app, ["cmd", '["a", "b", "c"]'])
        assert res.exit_code == 0
        assert received == ["a", "b", "c"]

    def test_variadic_argument_required(self) -> None:
        app = agentyper.Agentyper(name="app")
        received: list[str] = []

        @app.command()
        def cmd(files: list[str] = agentyper.Argument(...)) -> None:
            """Accept variadic files."""
            received.extend(files)

        res = runner.invoke(app, ["cmd", "a.txt", "b.txt", "c.txt"])
        assert res.exit_code == 0
        assert received == ["a.txt", "b.txt", "c.txt"]

    def test_variadic_argument_optional(self) -> None:
        app = agentyper.Agentyper(name="app")
        received: list[str] = []

        @app.command()
        def cmd(files: list[str] = agentyper.Argument(None)) -> None:
            """Accept optional variadic files."""
            if files:
                received.extend(files)

        res = runner.invoke(app, ["cmd", "x.txt", "y.txt"])
        assert res.exit_code == 0
        assert received == ["x.txt", "y.txt"]

        res_empty = runner.invoke(app, ["cmd"])
        assert res_empty.exit_code == 0
        assert received == ["x.txt", "y.txt"]  # no change

    def test_variadic_argument_typed(self) -> None:
        app = agentyper.Agentyper(name="app")
        received: list[Path] = []

        @app.command()
        def cmd(files: list[Path] = agentyper.Argument(...)) -> None:
            """Accept variadic Path files."""
            received.extend(files)

        res = runner.invoke(app, ["cmd", "f1.txt", "f2.txt"])
        assert res.exit_code == 0
        assert received == [Path("f1.txt"), Path("f2.txt")]

    def test_dry_run_flag(self) -> None:
        app = agentyper.Agentyper(name="app")
        calls = []

        @app.command(mutating=True)
        def delete(name: str, dry_run: bool = False) -> None:
            """Delete something."""
            calls.append(dry_run)

        res = runner.invoke(app, ["delete", "alice", "--dry-run"])
        assert res.exit_code == 0
        assert calls == [True]

    def test_dry_run_flag_in_sub_app(self) -> None:
        app = agentyper.Agentyper(name="app")
        sub = agentyper.Agentyper(name="price")
        calls: list[bool] = []

        @sub.command(mutating=True)
        def fetch(symbol: str, dry_run: bool = False) -> None:
            """Fetch price."""
            calls.append(dry_run)

        app.add_agentyper(sub, name="price")

        res = runner.invoke(app, ["price", "fetch", "AAPL", "--dry-run"])
        assert res.exit_code == 0
        assert calls == [True]

    def test_dry_run_in_schema(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command(mutating=True)
        def delete(name: str) -> None:
            """Delete something."""

        schema = app.get_schema()
        assert "dry_run" in schema["commands"]["delete"]["input_schema"]["properties"]
        assert schema["commands"]["delete"]["mutating"] is True

    def test_danger_level_in_schema(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def read(name: str) -> None:
            """Read something."""

        @app.command(mutating=True)
        def write(name: str) -> None:
            """Write something."""

        @app.command(danger_level="destructive")
        def nuke(name: str) -> None:
            """Nuke something."""

        schema = app.get_schema()
        assert schema["commands"]["read"]["danger_level"] == "safe"
        assert schema["commands"]["write"]["danger_level"] == "mutating"
        assert schema["commands"]["nuke"]["danger_level"] == "destructive"

    def test_exit_codes_in_command_schema(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd(name: str) -> None:
            """A command."""

        schema = app.get_schema()
        cmd_schema = schema["commands"]["cmd"]
        assert "exit_codes" in cmd_schema
        assert "0" in cmd_schema["exit_codes"]
        assert cmd_schema["exit_codes"]["0"]["name"] == "SUCCESS"

    def test_required_option_in_schema(self) -> None:
        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd(api_key: str = agentyper.Option(..., help="API key")) -> None:
            """Needs a key."""

        schema = app.get_schema()
        assert "api_key" in schema["commands"]["cmd"]["input_schema"]["required"]

    def test_pydantic_validation_error_exits_one(self) -> None:
        from pydantic import BaseModel, field_validator  # noqa: PLC0415

        class Input(BaseModel):
            value: int

            @field_validator("value")
            @classmethod
            def must_be_positive(cls, v: int) -> int:
                if v <= 0:
                    raise ValueError("must be positive")
                return v

        app = agentyper.Agentyper(name="app")

        @app.command()
        def cmd(value: int) -> None:
            """Validate."""
            Input(value=value)

        res = runner.invoke(app, ["cmd", "0"])
        assert res.exit_code == agentyper.EXIT_VALIDATION


# ---------------------------------------------------------------------------
# Built-in exec command tests
# ---------------------------------------------------------------------------


def _make_exec_app() -> agentyper.Agentyper:
    app = agentyper.Agentyper(name="myapp")

    @app.command()
    def greet(name: str) -> None:
        """Greet someone."""
        agentyper.output({"name": name, "message": f"Hello {name}"})

    @app.command(mutating=True)
    def create(label: str, dry_run: bool = False) -> None:
        """Create a resource."""
        agentyper.output({"label": label, "dry_run": dry_run})

    return app


def _make_ping_app() -> agentyper.Agentyper:
    app = agentyper.Agentyper(name="myapp")

    @app.command()
    def ping(prefix: str = "hi") -> None:
        agentyper.output({"prefix": prefix})

    return app


def _exec_stdin(
    app: agentyper.Agentyper, lines: list[dict], flags: list[str] | None = None
) -> Result:
    """Helper: invoke exec with a fake JSONL stdin; returns the runner Result."""
    stdin_text = "\n".join(json.dumps(line) for line in lines) + "\n"
    with patch("sys.stdin", io.StringIO(stdin_text)):
        return runner.invoke(app, ["exec"] + (flags or []))


def _output_lines(res: Result) -> list[str]:
    return [line for line in res.stdout.strip().splitlines() if line]


class TestExecCommand:
    def test_exec_appears_in_help(self) -> None:
        app = _make_exec_app()
        res = runner.invoke(app, ["--help"])
        assert "exec" in (res.stdout + res.stderr)

    def test_exec_disabled_via_flag(self) -> None:
        app = agentyper.Agentyper(name="myapp", exec=False)

        @app.command()
        def greet(name: str) -> None:
            """Greet."""

        res = runner.invoke(app, ["--help"])
        assert "exec" not in (res.stdout + res.stderr)

    def test_exec_single_line_success(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(app, [{"_cmd": "greet", "name": "Alice"}])
        assert res.exit_code == 0
        lines = _output_lines(res)
        assert len(lines) == 1
        out = json.loads(lines[0])
        assert out["ok"] is True
        assert out["exit_code"] == 0
        assert out["line"] == 1
        assert out["cmd"] == "greet"
        assert out["result"]["data"]["name"] == "Alice"

    def test_exec_multiple_lines(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(
            app,
            [
                {"_cmd": "greet", "name": "Alice"},
                {"_cmd": "greet", "name": "Bob"},
            ],
        )
        assert res.exit_code == 0
        lines = _output_lines(res)
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["line"] == 1
        assert second["line"] == 2
        assert first["result"]["data"]["name"] == "Alice"
        assert second["result"]["data"]["name"] == "Bob"

    def test_exec_skips_blank_lines(self) -> None:
        app = _make_exec_app()
        stdin_text = "\n" + json.dumps({"_cmd": "greet", "name": "Alice"}) + "\n\n"
        with patch("sys.stdin", io.StringIO(stdin_text)):
            res = runner.invoke(app, ["exec"])
        assert res.exit_code == 0
        lines = _output_lines(res)
        assert len(lines) == 1

    def test_exec_stops_on_error_by_default(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(
            app,
            [
                {"_cmd": "greet", "name": "Alice"},
                {"_cmd": "greet"},  # missing required 'name' → arg error
                {"_cmd": "greet", "name": "Bob"},
            ],
        )
        assert res.exit_code != 0
        lines = _output_lines(res)
        # Only 2 output lines: line 1 succeeded, line 2 failed and exec stopped
        assert len(lines) == 2
        assert json.loads(lines[0])["ok"] is True
        assert json.loads(lines[1])["ok"] is False

    def test_exec_ignore_errors_continues(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(
            app,
            [
                {"_cmd": "greet", "name": "Alice"},
                {"_cmd": "greet"},  # fails
                {"_cmd": "greet", "name": "Bob"},
            ],
            flags=["--ignore-errors"],
        )
        assert res.exit_code == int(agentyper.ExitCode.PARTIAL_FAILURE)
        lines = _output_lines(res)
        assert len(lines) == 3
        assert json.loads(lines[0])["ok"] is True
        assert json.loads(lines[1])["ok"] is False
        assert json.loads(lines[2])["ok"] is True

    def test_exec_invalid_json_line(self) -> None:
        app = _make_exec_app()
        stdin_text = "not valid json\n"
        with patch("sys.stdin", io.StringIO(stdin_text)):
            res = runner.invoke(app, ["exec"])
        assert res.exit_code != 0
        out = json.loads(res.stdout.strip())
        assert out["ok"] is False
        assert "error" in out

    def test_exec_missing_cmd_field(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(app, [{"name": "Alice"}])
        assert res.exit_code != 0
        out = json.loads(res.stdout.strip())
        assert out["ok"] is False
        assert "_cmd" in out["error"]

    def test_exec_opts_forwarded_as_flags(self) -> None:
        app = _make_exec_app()
        dry_runs: list[bool] = []

        @app.command(mutating=True)
        def update(label: str, dry_run: bool = False) -> None:
            """Update."""
            dry_runs.append(dry_run)
            agentyper.output({"label": label})

        res = _exec_stdin(app, [{"_cmd": "update", "_opts": {"dry_run": True}, "label": "x"}])
        assert res.exit_code == 0
        assert dry_runs == [True]

    def test_exec_dry_run_flag_forwarded_to_mutating(self) -> None:
        app = _make_exec_app()
        dry_runs: list[bool] = []

        @app.command(mutating=True)
        def write(label: str, dry_run: bool = False) -> None:
            """Write."""
            dry_runs.append(dry_run)
            agentyper.output({"label": label})

        res = _exec_stdin(app, [{"_cmd": "write", "label": "x"}], flags=["--dry-run"])
        assert res.exit_code == 0
        assert dry_runs == [True]

    def test_exec_dry_run_flag_not_forwarded_to_safe(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(app, [{"_cmd": "greet", "name": "Alice"}], flags=["--dry-run"])
        # safe command: --dry-run is NOT forwarded → no argparse error
        assert res.exit_code == 0

    def test_exec_dot_path_routes_to_sub_app(self) -> None:
        app = agentyper.Agentyper(name="root")
        sub = agentyper.Agentyper(name="account")
        names: list[str] = []

        @sub.command()
        def create(name: str) -> None:
            """Create account."""
            names.append(name)
            agentyper.output({"name": name})

        app.add_agentyper(sub, name="account")

        res = _exec_stdin(app, [{"_cmd": "account.create", "name": "Assets:Bank"}])
        assert res.exit_code == 0
        assert names == ["Assets:Bank"]
        out = json.loads(res.stdout.strip())
        assert out["ok"] is True

    def test_exec_all_succeed_exits_zero(self) -> None:
        app = _make_exec_app()
        res = _exec_stdin(
            app,
            [
                {"_cmd": "greet", "name": "A"},
                {"_cmd": "greet", "name": "B"},
                {"_cmd": "greet", "name": "C"},
            ],
        )
        assert res.exit_code == 0

    def test_exec_extra_args_forwarded_to_commands(self) -> None:
        res = _exec_stdin(
            _make_ping_app(),
            [{"_cmd": "ping"}, {"_cmd": "ping"}],
            flags=["--prefix", "hello"],
        )
        assert res.exit_code == 0
        lines = _output_lines(res)
        assert len(lines) == 2
        for line in lines:
            out = json.loads(line)
            assert out["ok"] is True
            assert out["result"]["data"]["prefix"] == "hello"

    def test_exec_per_line_opts_override_extra_args(self) -> None:
        res = _exec_stdin(
            _make_ping_app(),
            [
                {"_cmd": "ping"},
                {"_cmd": "ping", "_opts": {"prefix": "override"}},
            ],
            flags=["--prefix", "global"],
        )
        assert res.exit_code == 0
        lines = _output_lines(res)
        out0 = json.loads(lines[0])
        out1 = json.loads(lines[1])
        assert out0["result"]["data"]["prefix"] == "global"
        assert out1["result"]["data"]["prefix"] == "override"
