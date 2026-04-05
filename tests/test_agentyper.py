"""Smoke tests for agentyper library."""

from __future__ import annotations

import json

import agentyper
from agentyper.testing import CliRunner

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
