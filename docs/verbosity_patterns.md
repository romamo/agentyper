# CLI Verbosity Design Patterns: `-vv` vs `-d`

When designing a Command Line Interface (CLI), Choosing between **stacked verbosity flags (`-vv`)** and a **binary debug flag (`-d` / `--debug`)** is a critical architectural decision. Modern CLI tools (like `uv`, `pytest`, and `ssh`) heavily favor the `-vv` approach for several technical and ergonomic reasons.

## 1. Granularity and Scalability

### Stacked Verbosity (`-v`, `-vv`, `-vvv`)
Provides progressive levels of logs, allowing users to dial in exactly how much information they need.
- **Default (No flag):** `WARNING` / `ERROR`. Quiet output, only showing actionable issues.
- **`-v`:** `INFO`. High-level progress reporting (e.g., "Deleted 5 files").
- **`-vv`:** `DEBUG`. Detailed internal state (e.g., "Deleting file A... Success").
- **`-vvv`:** `TRACE`. Raw payloads, network request bodies, or database query logs.

### Binary Debug (`-d`)
Binary flags are strictly On/Off. If the debug output is too noisy, the user has no way to see "just the important updates" without the developer adding a second, separate `--verbose` flag later, leading to fragmented logic.

## 2. Short-Flag Contention

Single-letter flags are a scarce resource.
- **`-v`** is almost universally recognized as `--verbose` or `--version`. It rarely conflicts with domain-specific logic.
- **`-d`** is one of the most highly contested flags in CLI design. Depending on your tool's domain, you often need it for:
    - `--directory` (File system tools)
    - `--delete` (CRUD operations)
    - `--detach` (Process management)
    - `--dry-run` (Execution safety)
    - `--data` (Network/API tools)

By avoiding `-d` for debugging, you keep it available for core business features.

## 3. Agentic Execution & Tool Calling

For AI Agents interacting with your CLI:
- **Integer Schema:** You can expose verbosity as an integer `{"type": "integer", "description": "Verbosity level 1-3"}`. If an agent's task fails, it can autonomously increment this value to gather more context.
- **Boolean Schema:** A binary `--debug` flag gives the agent an "all or nothing" choice, which might overwhelm the LLM's context window with thousands of lines of trace logs when it only needed high-level info.

## 4. Implementation Patterns (Python)

### Using `argparse` (Standard Library)
The `action="count"` parameter makes implementing stacked flags trivial.

```python
import argparse
import logging

parser = argparse.ArgumentParser()
parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")
args = parser.parse_args()

# Map count to logging level
levels = [logging.WARNING, logging.INFO, logging.DEBUG]
level = levels[min(args.verbose, len(levels) - 1)]
logging.basicConfig(level=level)
```

### Using `Typer` / `Click`
```python
import typer

def main(verbose: int = typer.Option(0, "--verbose", "-v", count=True)):
    if verbose >= 2:
        print("Deep debug mode")
    elif verbose >= 1:
        print("Info mode")
```

## Summary Recommendation
Use **`-v`, `-vv`, `-vvv`**. It provides a clean, standard ladder for log levels, prevents flag naming conflicts, and creates a more robust interface for both humans and AI agents.
