---
name: agent-dx-cli-scale
description: A scoring scale for evaluating how well a CLI is designed for AI agents, based on the "Rewrite Your CLI for AI Agents" principles.
---

# Agent DX CLI Scale

> **Change log (v2):** 7 axes → 9 axes (0–21 → 0–27). Axes 1, 2, 3, 5, 6 amended. Axes 7, 8, 9 are new. Bonus section expanded.
> Lines and sections marked `[+]` are additions or amendments to the original scale.

Use this skill to **evaluate any CLI** against the principles of agent-first design. Score each axis from 0–3, then sum for a total between 0–27.

> Human DX optimizes for discoverability and forgiveness.
> Agent DX optimizes for predictability and defense-in-depth.

---

## Scoring Axes

### 1. Machine-Readable Output

Can an agent parse ~~the CLI's output~~ **both results and errors** without heuristics? `[+]`

| Score | Criteria |
| ----- | -------- |
| 0 | Human-only output (tables, color codes, prose). **Errors are plain-text strings.** `[+]` No structured format available. |
| 1 | `--output json` or equivalent exists but is incomplete or inconsistent across commands. **Errors are still unstructured.** `[+]` |
| 2 | Consistent JSON output across all commands. Errors also return structured JSON **with field location, message, and type. No ANSI codes in non-TTY.** `[+]` |
| 3 | NDJSON streaming for paginated results. Structured output **and structured errors** are the default in non-TTY (piped) contexts **— agent never needs to remember `--format json`. Diagnostic/progress output is separated to stderr unconditionally.** `[+]` |

---

### 2. Raw Payload Input

Can an agent send the full API payload without translation through bespoke flags?

| Score | Criteria |
| ----- | -------- |
| 0 | Only bespoke flags. No way to pass structured input. |
| 1 | Accepts `--json` or stdin JSON for some commands, but most require flags. |
| 2 | All mutating commands accept a raw JSON payload that maps directly to the underlying API schema. **`--json -` for stdin piping.** `[+]` |
| 3 | Raw payload is first-class alongside convenience flags. The agent can use the API schema as documentation with zero translation loss. **Exposes an MCP (stdio JSON-RPC) surface from the same binary, eliminating shell escaping entirely for agent callers.** `[+]` |

---

### 3. Schema Introspection

Can an agent discover what the CLI accepts **— and what it returns —** at runtime? `[+]`

| Score | Criteria |
| ----- | -------- |
| 0 | Only `--help` text. No machine-readable schema. |
| 1 | `--schema` or `describe` for some commands **(input schema only)**, but incomplete. `[+]` |
| 2 | Full **input and output** schema for all commands — params, types, required fields, **return shape** — as JSON. **Agent knows what it sends and what it gets back.** `[+]` |
| 3 | **Schema includes `max_retries` per error class and `alternative_commands` for capability gaps.** `[+]` Live, runtime-resolved schemas that always reflect the current version. Includes enums, scopes, and nested types. |

---

### 4. Context Window Discipline

Does the CLI help agents control response size to protect their context window?

| Score | Criteria |
| ----- | -------- |
| 0 | Returns full API responses with no way to limit fields or paginate. |
| 1 | Supports `--fields` or field masks on some commands. |
| 2 | Field masks on all read commands. Pagination with **`--limit` and cursor/page controls on all list commands.** `[+]` |
| 3 | **NDJSON streaming pagination (agent processes records before stream ends).** `[+]` Explicit guidance in skill files on field mask usage. **Default `--limit` prevents unbounded responses.** `[+]` The CLI actively protects the agent from token waste. |

> **Pipe as context bypass `[+]`:** When composing `toolA | toolB`, data moves directly between
> processes — it never enters the agent's context window. For large dataset transformations
> (e.g. `search … | collection add`), Unix pipes are the most token-efficient architecture possible.
> A `--count` flag on read commands enables a pre-flight scope check: the agent reads the count,
> decides if the scope is acceptable, then pipes if so — without consuming the actual records.
> Design read commands to support both direct consumption (`--format json`) and blind piping
> (`--count` + pipe) as first-class modes.

---

### 5. Input Hardening

Does the CLI defend against the specific ways agents fail — **confident wrongness, not typos?** `[+]`

| Score | Criteria |
| ----- | -------- |
| 0 | No input validation beyond basic type checks. |
| 1 | Validates some inputs**, but errors are flat strings and offer no correction path.** `[+]` |
| 2 | Rejects control characters, path traversals (`../`), percent-encoded segments (`%2e`), and embedded query params (`?`, `#`) in resource IDs. **Validation errors identify the exact failing field with the rejected value.** `[+]` |
| 3 | All of the above, plus: **`suggestions` array in every entity-not-found error (fuzzy-matched alternatives). `expected_format` and `example` in every constraint violation.** `[+]` Output path sandboxing to CWD. **The agent can self-correct on the first retry without guessing.** `[+]` |

---

### 6. Safety Rails

Can agents validate before acting? **Are irreversible operations protected?** `[+]`

| Score | Criteria |
| ----- | -------- |
| 0 | No dry-run mode. **Mutations execute immediately with no preview.** `[+]` |
| 1 | `--dry-run` exists for some mutating commands. |
| 2 | `--dry-run` for all mutating commands. **Dry-run returns the same structured response shape as a real write — agent can verify intent without side effects. All write commands return the created/mutated entity on success (never silence).** `[+]` |
| 3 | Dry-run plus response sanitization to defend against prompt injection embedded in fetched data (payee names, descriptions, tags). **`"next_steps"` field on success responses guides the agent's continuation.** `[+]` The full request→response loop is defended. |

---

### 7. Retry & Recovery Hints `[NEW AXIS]`

Does the CLI tell the agent exactly what to do next on every outcome?

| Score | Criteria |
| ----- | -------- |
| 0 | Errors are strings. Agent must parse prose to determine whether to retry or abort. |
| 1 | Errors include a machine-readable `error_type` field. Agent can branch on type without parsing message text. |
| 2 | Validation errors include `suggested_fix` (which argument, what value range, an example). Transient errors include `retry_after_seconds`. Agent can self-correct or back off without a retry loop. |
| 3 | Schema declares `max_retries` per error class — prevents infinite loops. `alternative_commands` in not-applicable errors points to the right tool. Success responses include `"next_steps"`. The CLI is a state machine: every output tells the agent what comes next. |

---

### 8. Interactive Safety `[NEW AXIS]`

Does the CLI guarantee non-blocking execution in agent/piped contexts?

| Score | Criteria |
| ----- | -------- |
| 0 | Any `confirm()`, `prompt()`, or password input blocks the subprocess indefinitely in non-TTY. Hard failure with no recovery. |
| 1 | `--yes` flag auto-confirms for some commands. |
| 2 | `--yes`/`--no` on all commands. All interactive calls resolve without blocking in non-TTY. Spinners and progress bars are suppressed in non-TTY. |
| 3 | `--answers '{"confirms":[true],"prompts":["value"]}'` pre-supplies all answers for complex multi-step flows. Environment variable overrides (`TOOL_YES=1`, `TOOL_ANSWERS=path`). `launch(url)` emits a JSON side-effect event to stderr instead of opening a browser. The CLI is fully scriptable with zero interactive surface. |

---

### 9. Semantic Exit Codes `[NEW AXIS]`

Can agents branch on outcomes without parsing error message text?

| Score | Criteria |
| ----- | -------- |
| 0 | Binary `0`/`1`. Validation failures and system failures are indistinguishable by exit code. |
| 1 | Some commands use consistent codes, but not all error paths are covered. |
| 2 | `0` success · `1` validation error (fix input, retry) · `2` system error (abort, report to user) — consistently enforced across all commands and error paths. |
| 3 | Exit code is also embedded in the structured error JSON (`"exit_code": 1`) so agents parsing stdout don't need a separate process code check. Transient/rate-limit codes (`30–39`) carry `retry_after_seconds` in the error body. |

---

## Interpreting the Total

| Range | Rating | Description |
| ----- | ------ | ----------- |
| ~~0–5~~ 0–7 | **Human-only** | Built for humans. Agents will **hang on prompts,** `[+]` hallucinate inputs, and **have no recovery path.** `[+]` |
| ~~6–10~~ 8–14 | **Agent-tolerant** | Agents can use it, but they'll waste tokens, make avoidable errors, and require heavy prompt engineering. |
| ~~11–15~~ 15–20 | **Agent-ready** | Solid agent support. Structured I/O, input validation, schema introspection, **exit codes.** `[+]` A few gaps remain. |
| ~~16–21~~ 21–27 | **Agent-first** | Purpose-built for agents. **Every output is a state transition. The CLI defends the agent from its own mistakes.** `[+]` |

---

## Bonus: Multi-Surface Readiness

Not scored, but note whether the CLI exposes multiple agent surfaces from the same binary:

- [ ] **MCP (stdio JSON-RPC)** — typed tool invocation, no shell escaping, **eliminates Axes 2 and 8 problems entirely** `[+]`
- [ ] ~~**Extension / plugin install**~~ **Installable skill file** `[+]` — `<tool>.skill.md` with YAML frontmatter, loadable into agent system context at conversation start **(ambient vs discoverable knowledge)** `[+]`
  - *Context cost:* In Claude Code, every installed skill's name+description loads at startup. Ship 1–3 broad skills, not 100 narrow ones. Full skill content only loads when invoked. *— via HN: sheept, danw1979*
- [ ] **Headless auth** — env vars for tokens/credentials, no browser redirect required
- [ ] **`--generate-skill`** `[NEW]` — auto-generates a versioned skill file from live `--schema` output, preventing version drift
- [ ] **In-CLI semantic doc search** `[NEW]` — `docs` (list), `docs --path FILE` (fetch), `search "query"` (embedding search across doc corpus). Collapses large documentation into relevant excerpts; agent asks a question in natural language rather than loading entire docs. *— via HN: jsunderland323*
- [ ] **Structured invocation log** `[NEW]` — emits `{"event":"invoked","command":"...","args":{...},"ts":"..."}` to stderr so agent wrappers can build an audit trail of what ran, when, and with what arguments. Addresses the accountability gap in long autonomous workflows. *— via HN: stratifyintel*
