# Agent Requirements for Execution CLI Tools

When Large Language Models (LLMs) or AI Agents interact with command-line interfaces, they behave very differently than human operators. To be "Agent-Ready," a CLI tool must satisfy these fundamental requirements:

## 1. Schema Introspection (Discoverability)
Agents cannot read a man page or `--help` text the way a human does and reliably infer parameter constraints. They require a strict, machine-readable JSON Schema (e.g., OpenAPI format) that perfectly describes the tool inputs (types, defaults, enums), which they can feed directly into their internal Function Calling / Tool Calling loops.

## 2. Deterministic & Non-Blocking Execution
Standard terminal environments use interactive prompts like `[y/N]` or `Please enter password:`. An agent attached via `subprocess` or `stdio` will hang indefinitely waiting for standard input. True agent CLIs must provide deterministic ways to bypass interactive prompts programmatically (e.g., `--yes`).

## 3. Structured Outputs
Terminals are often designed for human eyes, utilizing ANSI color codes, progress bars, and ASCII tables. Agents hallucinate when trying to parse this text. They need outputs to be strictly structured (JSON/CSV) so they can deterministically parse the result.

## 4. Actionable Structured Errors
When a human supplies `--count "foo"`, standard CLIs throw a string exception: `Error: "foo" is not a valid integer`. An agent needs a structured JSON error indicating exactly which parameter failed (e.g., `{"loc": ["count"], "msg": "Input should be a valid integer"}`) so it can auto-correct its payload without hallucination.

## 5. Semantic Exit Codes
Agents rely on exit codes to branch logic. 
* A `0` means success. 
* A `1` (Validation Error) tells the agent: *"You made a mistake, fix your argument and retry."* 
* A `2` (System Error) tells the agent: *"Something is broken externally, abort and report to the user."* 
## 6. Granular Verbosity Control
Agents should be able to control the level of detail they receive. While binary `--debug` flags are common, stacked verbosity flags (e.g., `-v`, `-vv`) are preferred for better scalability and to avoid flag contention with other domain-specific options.

Read more about [CLI Verbosity Design Patterns (-vv vs -d)](verbosity_patterns.md).
