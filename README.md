# agentyper

**Agent-first Python CLI library** — built on `argparse` + `pydantic`, Typer-compatible.

> *Typer was built for the era when type hints changed Python.*  
> *agentyper is built for the era when AI agents changed how software is operated.*

## Why Agentyper?

**Typer was built for humans. Agents need something different.** 

Typer revolutionized CLI development by elegantly using type hints. However, because it is built on `Click` and `Rich`, it is fundamentally optimized for human eyeballs. When interacting with standard CLIs, AI Agents struggle: they cannot organically discover the tool's schema, they get blocked by interactive prompts (`[y/N]`), they hallucinate when parsing ANSI-colored tables, and they fail to self-correct when errors are returned as flat strings.

**Agentyper** keeps the beloved Developer Experience (DX) of Typer but replaces the execution engine with `argparse` and `Pydantic`. 
With a one-line switch (`import agentyper as typer`), your CLI instantly yields OpenAPI/JSON schemas, strict Pydantic structured errors, and deterministic programmatic overrides for interactive features.

📖 **Learn more in our docs:**
* **[For Agents: How to use an agentyper CLI](docs/for-agents.md)** — schema discovery, format flags, error JSON, bypass flags
* **[For Developers: Building agent-friendly CLIs](docs/for-developers.md)** — best practices, output patterns, testing
* **[Agent Requirements for CLI Tools](docs/agent_cli_requirements.md)**
* **[Why Agentyper? (Alternatives & Comparison Matrix)](docs/why_agentyper.md)**

> agentyper implements the **[CLI Agent Spec](https://github.com/romamo/cli-agent-spec)** — a formal specification for agent-ergonomic CLI tools.

## Install

```bash
pip install agentyper
```

## Quick Start

**Absolute minimum:**

Create a `main.py`:
```python
def main(name: str):
    print(f"Hello {name}")
```

Run it without importing anything:
```bash
agentyper main.py run Camila
```

**Single function (like `typer.run()`):**

```python
import agentyper

def search(ticker: str, limit: int = 10):
    """Search securities by ticker."""
    results = service.search(ticker, limit)
    agentyper.output(results)   # routes via --format automatically

agentyper.run(search)
```

**Multi-command app:**

```python
import agentyper

app = agentyper.Agentyper(name="my-tool", version="1.0.0")

@app.command()
def search(ticker: str, limit: int = agentyper.Option(10, help="Max results")):
    """Search securities."""
    agentyper.output(service.search(ticker, limit))

@app.command()
def delete(name: str):
    """Delete a record."""
    if agentyper.confirm(f"Delete '{name}'?"):
        service.delete(name)

app()
```

**Typer migration — one line:**

```python
# import typer          ← before
import agentyper as typer  # ← after; everything else stays identical
```

## What Agents Get for Free

Every command automatically gains:

```bash
my-tool --schema                  # full JSON Schema of the entire app
my-tool search --schema           # JSON Schema for this command's params
my-tool search AAPL --format csv  # 4× cheaper output than table
my-tool search AAPL --format json # structured JSON output
my-tool delete alice --yes        # skip confirm() in agent mode
my-tool wizard --answers '{"confirms":[true],"prompts":["Alice","admin"]}'
```

**If you are an agent consuming an agentyper CLI, read [docs/for-agents.md](docs/for-agents.md)** for schema discovery, error handling, and the full flag reference.  
**If you are building a CLI with agentyper, read [docs/for-developers.md](docs/for-developers.md)** for patterns, best practices, and a pre-ship checklist.

## Agent Ergonomics

| Feature | agentyper | Typer |
|---|---|---|
| `--schema` on every command | ✅ automatic | ❌ manual |
| `--format json/csv/table` | ✅ automatic | ❌ manual |
| Structured JSON errors | ✅ automatic | ❌ free text |
| Exit code taxonomy (0/1/2) | ✅ | ❌ 0 or 1 |
| Interactive features in agent mode | ✅ `--yes/--answers` bypass | ❌ blocks |
| `isatty()` auto-format detection | ✅ | ❌ |
| Dependencies | argparse + pydantic | Click + Typer |

## Typer Compatibility Matrix

`agentyper` implements Typer's core API. For most CLIs, `import agentyper as typer` works flawlessly. Because it is built from scratch on `argparse` + `Pydantic` (for LLM reliability) instead of `Click`, there are some differences.

| Feature / API | Status | Notes |
|---|---|---|
| `@app.command()`, `@app.callback()` | ✅ Supported | Core app routing works identically. |
| `Option()`, `Argument()` | ✅ Supported | Core args mapping (`default`, `help`, etc.) |
| Type Hint Extraction | ✅ Supported | Uses Pydantic for robust validation. |
| Interactive Prompts (`confirm()`) | ✅ Supported | Enhanced with non-blocking agent overrides. |
| `typer.Context` (`ctx`) | ✅ Supported | Invocation-scoped context exposes `ctx.format`, `ctx.runtime`, `ctx.root`, `ctx.params`, and `ctx.obj`. `click`-specific methods (`ctx.forward()`, `ctx.meta`) still do not exist. |
| `click` Parameter Types | ❌ Unsupported | Fully replaced by Pydantic. Use `Literal["A"]` instead of `click.Choice`. |
| `typer.style()`, colors | ❌ Unsupported | Removed. Agents prefer plain text or structured JSON. |
| Custom `Click` logic | ❌ Unsupported | Executed purely via standard `argparse`. |

## Exit Codes

```python
agentyper.EXIT_SUCCESS    = 0  # success
agentyper.EXIT_VALIDATION = 1  # bad input — agent should retry with correction
agentyper.EXIT_SYSTEM     = 2  # system error — agent should abort
```

## Interactive Features

All interactive features from Typer work identically in a terminal.
In agent/non-TTY mode, they resolve without blocking:

```bash
# Human terminal: asks interactively
my-tool delete alice

# Agent: auto-confirm via flag
my-tool delete alice --yes

# Agent: pre-supply all answers
my-tool wizard --answers '{"confirms":[true,false],"prompts":["Alice","admin"]}'

# Agent: pipe answers from stdin
echo '{"confirms":[true]}' | my-tool delete alice --answers -
```

## Invocation Context

Commands and callbacks can accept `ctx: agentyper.Context` to inspect the resolved invocation state:

```python
@app.command()
def sync(ctx: agentyper.Context, target: str):
    client = build_client(ctx)
    agentyper.output({"target": target, "format": ctx.format})
```

Helper code can also read the active invocation context without manually threading it through every call:

```python
def build_client(ctx: agentyper.Context | None = None) -> Client:
    ctx = ctx or agentyper.get_current_context()
    return Client(timeout_ms=ctx.runtime.timeout_ms, verbose=ctx.runtime.verbosity > 0)
```

## License

MIT
