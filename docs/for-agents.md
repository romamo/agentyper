# Using an agentyper CLI as an Agent

This document is a concise reference for AI agents (and agent developers) consuming any CLI built with agentyper. Every agentyper CLI exposes a consistent, machine-readable interface — this is the contract.

agentyper implements the **[CLI Agent Spec](https://github.com/romamo/cli-agent-spec)**.

---

## 1. Discover the schema

Before calling any command, fetch the schema. It is always available and always current.

```bash
# Full app schema (all commands, input + output shapes)
<tool> --schema

# Single-command schema (only that command's inputs)
<tool> <command> --schema
```

The schema is a JSON object:

```json
{
  "name": "my-tool",
  "version": "1.2.0",
  "commands": {
    "search": {
      "description": "Search securities by ticker.",
      "input_schema": {
        "type": "object",
        "properties": {
          "ticker": { "type": "string" },
          "limit":  { "type": "integer", "default": 10 }
        },
        "required": ["ticker"]
      },
      "output_schema": { ... }
    },
    "delete": {
      "mutating": true,
      "input_schema": {
        "properties": {
          "name":    { "type": "string" },
          "dry_run": { "type": "boolean", "default": false, "description": "Print without writing" }
        },
        "required": ["name"]
      }
    }
  }
}
```

Use `input_schema.required` to identify mandatory arguments. Use `mutating: true` to know when `--dry-run` is available.

---

## 2. Request structured output

agentyper auto-detects non-TTY context and defaults to JSON. You can also be explicit:

```bash
<tool> search AAPL --format json   # JSON object or array to stdout
<tool> search AAPL --format csv    # CSV with header row to stdout
<tool> search AAPL --format table  # Rich table (human use only)
```

**Environment variable:** `AGENTYPER_FORMAT=json` sets the default for all commands in the session.

In piped / non-TTY contexts, `--format json` is the default — you do not need to pass it explicitly.

---

## 3. Read structured errors

All errors go to **stderr**. In non-TTY context they are always JSON:

```json
{
  "error": true,
  "error_type": "ValidationError",
  "exit_code": 1,
  "errors": [
    {
      "field": "limit",
      "message": "Input should be a valid integer",
      "type": "int_parsing"
    }
  ]
}
```

Single-field errors:

```json
{
  "error": true,
  "error_type": "Error",
  "message": "Ticker 'XYZZY' not found",
  "exit_code": 1
}
```

---

## 4. Branch on exit codes

| Code | Meaning | Agent action |
|------|---------|--------------|
| `0` | Success | Continue |
| `1` | Validation error — bad input | Read `errors[].field` + `errors[].message`, correct the argument, retry |
| `2` | System error — external failure | Abort, report to user |

---

## 5. Bypass interactive prompts

agentyper CLIs never block in non-TTY. If a command contains confirmation or prompt steps:

```bash
# Auto-confirm all confirm() calls
<tool> delete alice --yes

# Auto-deny all confirm() calls
<tool> delete alice --no

# Pre-supply all answers (confirms queue + named prompts)
<tool> wizard --answers '{"confirms":[true,false],"prompts":{"enter_name":"Alice"}}'

# Positional answer queue (order-dependent)
<tool> wizard --answers '{"confirms":[true],"prompts":["Alice","admin"]}'

# Pipe answers from a file
<tool> wizard --answers answers.json

# Pipe from stdin
echo '{"confirms":[true]}' | <tool> delete alice --answers -
```

**Environment variables:**
- `AGENTYPER_YES=1` — equivalent to `--yes` globally
- `AGENTYPER_ANSWERS=<json-or-path>` — equivalent to `--answers` globally

If a `launch(url)` call would open a browser, it emits a JSON notice to stderr instead:

```json
{"side_effect": "open_url", "url": "https://...", "locate": false}
```

---

## 6. Verbosity

```bash
<tool> <command> -v    # INFO level logging to stderr
<tool> <command> -vv   # DEBUG level logging to stderr
```

Useful when a command fails unexpectedly and you need more context before retrying.

---

## 7. Dry-run for mutating commands

Commands marked `mutating: true` in the schema always accept `--dry-run`:

```bash
<tool> delete alice --dry-run   # previews the action, no side effects
```

Use dry-run to validate intent before committing. The response shape is identical to a real write.

---

## Quick reference

```
<tool> --schema                          discover all commands
<tool> <cmd> --schema                    discover one command's inputs
<tool> <cmd> [args] --format json        structured output
<tool> <cmd> [args] --yes                skip all confirm() calls
<tool> <cmd> [args] --no                 deny all confirm() calls
<tool> <cmd> [args] --answers '<json>'   pre-supply all interactive answers
<tool> <cmd> [args] --dry-run            preview mutating command
<tool> <cmd> [args] -v / -vv             verbose / debug logging
AGENTYPER_FORMAT=json                    set default output format
AGENTYPER_YES=1                            set global auto-confirm
AGENTYPER_ANSWERS=<json-or-path>           set global answers
```
