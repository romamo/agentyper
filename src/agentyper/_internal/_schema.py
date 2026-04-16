"""JSON Schema generation from Python function signatures via pydantic.TypeAdapter."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import TypeAdapter

from agentyper._internal._errors import EXIT_CODE_TABLE
from agentyper._internal._params import ArgumentInfo, OptionInfo

_GLOBAL_PARAMS = {"format_", "schema", "yes", "no", "answers", "verbose", "version", "_timeout_ms"}


def _slugify(text: str) -> str:
    """Convert prompt text to a dict key: 'Enter your name' → 'enter_your_name'."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema dict via pydantic TypeAdapter."""
    ta = TypeAdapter(annotation)
    return ta.json_schema(mode="serialization")


def fn_to_input_schema(fn: Callable) -> dict[str, Any]:
    """
    Generate a JSON Schema ``object`` representing a command's input parameters.

    Skips parameters named in ``_GLOBAL_PARAMS`` (injected by agentyper).

    Args:
        fn: The command handler function.

    Returns:
        A dict compatible with JSON Schema draft 2020-12 ``type: object``.
    """
    hints = get_type_hints(fn)

    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in _GLOBAL_PARAMS or name in ("ctx", "_ctx", "context"):
            continue

        # Resolve annotation
        annotation = hints.get(name, str)

        # Build base JSON Schema from type
        schema = _annotation_to_json_schema(annotation)

        # Resolve default value and help from OptionInfo / ArgumentInfo / raw default
        default = param.default
        help_text = ""

        if isinstance(default, (OptionInfo, ArgumentInfo)):
            if default.has_default and default.default is not ...:
                schema["default"] = default.default
            else:
                required.append(name)
            help_text = default.help or ""
        elif default is inspect.Parameter.empty or default is ...:
            required.append(name)
        else:
            schema["default"] = default

        if help_text:
            schema["description"] = help_text

        properties[name] = schema

    return {
        "type": "object",
        "description": inspect.cleandoc(fn.__doc__ or ""),
        "properties": properties,
        "required": required,
    }


def fn_return_schema(fn: Callable) -> dict[str, Any] | None:
    """
    Attempt to derive an output schema from a function's return type annotation.

    Returns ``None`` if no annotation is present or it cannot be resolved.
    """
    hints = get_type_hints(fn)

    ret = hints.get("return")
    if ret is None:
        return None

    ta = TypeAdapter(ret)
    return ta.json_schema(mode="serialization")


def build_app_schema(
    name: str,
    version: str | None,
    commands: dict[str, Any],
    sub_apps: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the top-level schema for a full Agentyper app.

    Args:
        name:      App/tool name.
        version:   Version string, if provided.
        commands:  Mapping of command name → CommandInfo.
        sub_apps:  Mapping of sub-app name → Agentyper instance.

    Returns:
        A JSON-serialisable schema dict.
    """
    exit_codes = {
        str(code): {
            "name": entry.name,
            "description": entry.description,
            "retryable": entry.retryable,
            "side_effects": entry.side_effects,
        }
        for code, entry in EXIT_CODE_TABLE.items()
    }
    schema: dict[str, Any] = {
        "name": name,
        "commands": {},
        "exit_codes": exit_codes,
    }
    if version:
        schema["version"] = version

    for cmd_name, cmd_info in commands.items():
        input_schema = fn_to_input_schema(cmd_info.fn)
        if cmd_info.mutating:
            input_schema = {
                **input_schema,
                "properties": {
                    **input_schema["properties"],
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Print without writing",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": (
                            "Caller-supplied key; duplicate calls with the same key"
                            " return the original result without re-executing"
                        ),
                    },
                },
            }
        entry: dict[str, Any] = {
            "description": inspect.cleandoc(cmd_info.fn.__doc__ or ""),
            "input_schema": input_schema,
            "danger_level": cmd_info.danger_level,
            "option_placement": cmd_info.option_placement,
            "exit_codes": {
                str(int(code)): {
                    "name": e.name,
                    "description": e.description,
                    "retryable": e.retryable,
                    "side_effects": e.side_effects,
                }
                for code, e in sorted(cmd_info.exit_codes.items())
            },
        }
        ret = fn_return_schema(cmd_info.fn)
        if ret is not None:
            entry["output_schema"] = ret
        if cmd_info.mutating:
            entry["mutating"] = True
        if cmd_info.requires_editor:
            entry["requires_editor"] = True
            entry["non_interactive_alternatives"] = cmd_info.non_interactive_alternatives
        schema["commands"][cmd_name] = entry

    for sub_name, sub_app in sub_apps.items():
        schema["commands"][sub_name] = sub_app.get_schema()

    return schema
