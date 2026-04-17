# Building Agent-Friendly CLIs with agentyper

This guide covers patterns and best practices for developers building CLIs with agentyper. Following these patterns means agents can use your CLI with zero prompt engineering.

agentyper implements the **[CLI Agent Spec](https://github.com/romamo/cli-agent-spec)**.

---

## 1. Output structured data — never `print()`

The simplest pattern: **return the value**. The framework routes it through `--format` automatically (JSON in non-TTY, table in TTY).

```python
@app.command()
def search(ticker: str, limit: int = Option(10)) -> list[Result]:
    """Search securities by ticker."""
    return service.search(ticker, limit)   # framework renders via --format
```

Use `agentyper.output()` directly when you need mid-function output or a table title:

```python
@app.command()
def report() -> None:
    """Multi-section report."""
    agentyper.output(service.get_summary(), title="Summary")
    agentyper.output(service.get_details(), title="Details")
```

Do not call `agentyper.output()` **and** return data — that prints twice.

Use `agentyper.echo()` only for human-facing status messages:

```python
agentyper.echo("Fetching data...")               # human status, goes to stdout
agentyper.echo("Warning: rate limited", err=True)  # warning to stderr
```

---

## 2. Annotate return types for output schema

If your command returns data, annotate the return type. agentyper uses it to populate `output_schema` in `--schema`, so agents know what shape to expect.

```python
from pydantic import BaseModel

class Security(BaseModel):
    ticker: str
    price: float
    name: str

@app.command()
def get(ticker: str) -> Security:
    """Fetch a single security."""
    return service.get(ticker)
```

Without a return annotation, `output_schema` is omitted from the schema. Agents can still use the command, but they cannot pre-validate the response shape.

---

## 3. Use structured errors — never `sys.exit(1)`

Use `agentyper.exit_error()` for all error conditions. It emits JSON to stderr in non-TTY and a Rich message in TTY.

```python
@app.command()
def get(ticker: str) -> Security:
    """Fetch a single security."""
    result = service.get(ticker)
    if result is None:
        agentyper.exit_error(
            f"Ticker '{ticker}' not found",
            code=agentyper.EXIT_VALIDATION,   # agent should retry with a different value
            field="ticker",
        )
    agentyper.output(result)
```

Exit code semantics:

| Code | Constant | When to use |
|------|----------|-------------|
| `0` | `EXIT_SUCCESS` | Command completed successfully |
| `1` | `EXIT_VALIDATION` | Bad input — agent can self-correct and retry |
| `2` | `EXIT_SYSTEM` | External failure — agent should abort and report |

Use `EXIT_VALIDATION` for anything the agent can fix (wrong value, missing field, constraint violation). Use `EXIT_SYSTEM` for things it cannot (network down, database unreachable, permission denied).

---

## 4. Mark mutating commands

```python
@app.command(mutating=True)
def delete(name: str, dry_run: bool = False) -> None:
    """Delete a record."""
    if dry_run:
        agentyper.echo(f"Would delete: {name}")
        return
    service.delete(name)
    agentyper.echo(f"Deleted: {name}")
```

`mutating=True`:
- Auto-injects `--dry-run` into the parser
- Sets `"mutating": true` and adds `dry_run` to `input_schema` in `--schema`
- Signals to agents that this command has side effects

Always implement actual dry-run behaviour. Agents will use it to verify intent before committing.

---

## 5. Use `Option(...)` for required options

Use `...` (Ellipsis) as the default for required options. This propagates to `input_schema.required` in the schema.

```python
@app.command()
def cmd(
    api_key: str = Option(..., envvar="MY_API_KEY", help="API key"),
    output_dir: Path = Option(..., help="Where to write results"),
) -> None:
    ...
```

Agents read `required` from the schema before calling. Missing required fields in the schema means agents will guess — or fail silently.

---

## 6. Make interactive features agent-safe

All of `confirm()`, `prompt()`, and `edit()` are safe in non-TTY context when called with `--yes`, `--no`, or `--answers`.

Those flags are always accepted by the parser, but they are shown in `--help` only when the app/command is interactive. agentyper will surface them automatically when it can detect interactive routines, and you can declare them explicitly when needed:

```python
app = agentyper.Agentyper(name="my-tool", interactive=True)


@app.command(interactive=True)
def setup() -> None:
    ...
```

Write interactive commands naturally:

```python
@app.command()
def publish(name: str) -> None:
    """Publish a draft."""
    if not agentyper.confirm(f"Publish '{name}'?"):
        agentyper.echo("Aborted.")
        return
    service.publish(name)
```

An agent calls it as:
```bash
my-tool publish "My Draft" --yes
```

For multi-step wizards with several prompts, document the expected `--answers` shape in the command's docstring:

```python
@app.command()
def setup() -> None:
    """
    Interactive setup wizard.

    Agent usage:
        --answers '{"prompts": {"enter_name": "Alice", "enter_role": "admin"}, "confirms": [true]}'
    """
    name = agentyper.prompt("Enter name")
    role = agentyper.prompt("Enter role")
    if agentyper.confirm("Save?"):
        service.save(name, role)
```

If your command reaches interactive behavior indirectly through helper functions, mark that command with `interactive=True` so the flags are visible in `--help` instead of just being silently accepted.

---

## 6.1 Declare timeout support only where it matters

`--timeout MS` is also capability-based. It appears only when timeout handling is enabled for the app or command.

```python
app = agentyper.Agentyper(name="my-tool", default_timeout_ms=30_000)


@app.command(timeout_ms=5_000)
def sync() -> None:
    ...
```

Use `enable_timeout=True` when you want the flag exposed even if the actual timeout value is resolved elsewhere. Leave timeout disabled for commands that do not need it so their help stays smaller and clearer.

---

## 7. Choose the right context pattern

Commands and callbacks can accept `ctx: agentyper.Context` directly:

```python
@app.command()
def sync(ctx: agentyper.Context, target: str) -> None:
    client = build_client(ctx)
    agentyper.output({"target": target, "format": ctx.format})
```

Use this rule of thumb:

- Pass `ctx` explicitly when invocation state is a meaningful dependency of the function.
- Use `agentyper.get_current_context()` in deep helper layers that need invocation-scoped state but would otherwise force `ctx` through unrelated call chains.
- If the helper may run outside a live CLI invocation, prefer an optional `ctx` argument or handle the `RuntimeError` from `get_current_context()`.

`agentyper.Context` is intentionally split by responsibility:

- `ctx.runtime` contains framework-resolved state such as format, verbosity, answers, and timeout.
- `ctx.params` contains parsed command parameters.
- `ctx.root` contains global/root flags.
- `ctx.obj` is mutable shared state owned by the application.

A practical hybrid pattern keeps command dependencies explicit while still making deep helpers easy to reuse and test:

```python
def emit_success(
    payload: dict,
    ctx: agentyper.Context | None = None,
) -> None:
    ctx = ctx or agentyper.get_current_context()
    agentyper.output(payload, format_=ctx.format_)


@app.command()
def run(ctx: agentyper.Context) -> None:
    emit_success({"status": "ok"}, ctx=ctx)
```

This keeps helpers convenient without turning invocation state into a hidden dependency everywhere.

---

## 8. Provide meaningful `help=` strings

The `help` text on `Option()` and `Argument()` appears as `description` in the JSON Schema. Agents use this to understand what a field means — treat it like API documentation, not UI copy.

```python
# Weak — no guidance for agents
limit: int = Option(10, help="Limit")

# Strong — agents know the constraint and default
limit: int = Option(10, help="Maximum number of results to return (1–1000)")
api_key: str = Option(..., help="API key from Settings > Developer. Required for all write operations.")
```

---

## 9. Name commands and flags consistently

Follow Unix conventions and the [CLI Agent Spec](https://github.com/romamo/cli-agent-spec) naming rules:

- Commands: `verb-noun` or `verb` — `create`, `delete`, `list`, `get`, `search`
- Flags: `--kebab-case` — `--output-dir`, `--dry-run`, `--api-key`
- Avoid abbreviations in flags agents will use (`--output-dir` not `--od`)
- Avoid positional-only arguments for options agents frequently override

---

## 10. Add version to your app

```python
app = agentyper.Agentyper(name="my-tool", version="1.2.0")
```

This enables `my-tool --version` and populates `"version"` in `--schema`. Agents can check version to detect schema drift between invocations.

---

## 11. Testing agent paths

Use `agentyper.testing.CliRunner` to test all agent-relevant paths:

```python
from agentyper.testing import CliRunner
import json

runner = CliRunner()

def test_schema_valid():
    result = runner.invoke(app, ["--schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert "commands" in schema

def test_json_output():
    result = runner.invoke(app, ["search", "AAPL", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["ticker"] == "AAPL"

def test_validation_error_is_structured():
    result = runner.invoke(app, ["search"])   # missing required arg
    assert result.exit_code != 0

def test_confirm_bypass():
    result = runner.invoke(app, ["delete", "alice", "--yes"])
    assert result.exit_code == 0

def test_dry_run():
    result = runner.invoke(app, ["delete", "alice", "--dry-run"])
    assert result.exit_code == 0
    # nothing actually deleted
```

Test every `--yes`, `--answers`, and `--dry-run` path explicitly. These are the paths agents exercise most.
Also test that interactive and timeout flags appear only on the commands that are meant to expose them.

---

## Checklist before shipping

- [ ] Every command has a docstring (becomes `description` in schema)
- [ ] Every `Option`/`Argument` has a `help=` string
- [ ] Required fields use `Option(...)` or `Argument(...)` — they appear in `input_schema.required`
- [ ] Structured output via `agentyper.output()`, not `print()`
- [ ] Structured errors via `agentyper.exit_error()`, not `sys.exit(1)`
- [ ] Mutating commands use `mutating=True` and respect `dry_run`
- [ ] Return type annotated for commands that produce data
- [ ] `app = Agentyper(name=..., version=...)` includes version
- [ ] `--schema` output tested in CI
- [ ] `--format json` output tested in CI
