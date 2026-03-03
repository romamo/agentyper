"""
Interactive session context and resolution hierarchy for agentyper.

Every interactive call (confirm, prompt, edit) resolves through this hierarchy:
  1. Explicit flag (--yes / --no / named param in --answers)
  2. Environment variable (AGENTER_YES, AGENTER_ANSWERS)
  3. Pre-supplied answers queue (from --answers JSON)
  4. Default value (if provided)
  5. TTY (ask the human if sys.stdin.isatty())
  6. Structured error (no path available)
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class InteractiveSession:
    """
    Holds all non-interactive answer sources for the current command invocation.

    Built by :meth:`from_parsed` after argparse processes the command's argv.
    """

    auto_yes: bool = False  # --yes flag
    auto_no: bool = False  # --no flag
    confirms_queue: list[bool] = dataclasses.field(default_factory=list)
    prompts_dict: dict[str, Any] = dataclasses.field(default_factory=dict)
    prompts_queue: list[Any] = dataclasses.field(default_factory=list)
    _has_answers: bool = dataclasses.field(default=False, repr=False)

    @classmethod
    def from_parsed(
        cls,
        auto_yes: bool,
        auto_no: bool,
        answers_raw: str | None,
    ) -> InteractiveSession:
        """
        Construct a session from parsed CLI arguments.

        Args:
            auto_yes:    Value of the ``--yes`` flag.
            auto_no:     Value of the ``--no`` flag.
            answers_raw: Raw ``--answers`` value (JSON string, file path, or ``"-"``).
        """
        session = cls(auto_yes=auto_yes, auto_no=auto_no)

        # Fallback to environment variable
        if answers_raw is None:
            answers_raw = os.getenv("AGENTER_ANSWERS")

        if answers_raw:
            session._load_answers(answers_raw)

        return session

    def _load_answers(self, raw: str) -> None:
        if raw == "-":
            data = json.load(sys.stdin)
        elif raw.startswith("{") or raw.startswith("["):
            data = json.loads(raw)
        else:
            data = json.loads(Path(raw).read_text())

        self._has_answers = True
        confirms = data.get("confirms", [])
        self.confirms_queue = list(confirms)

        prompts = data.get("prompts", {})
        if isinstance(prompts, list):
            self.prompts_queue = list(prompts)
        else:
            self.prompts_dict = dict(prompts)

    # ------------------------------------------------------------------
    # Resolution methods
    # ------------------------------------------------------------------

    def resolve_confirm(self, text: str, default: bool) -> bool | None:
        """
        Resolve a ``confirm()`` call without blocking on TTY.

        Returns the resolved value, or ``None`` if the human must be asked.
        """
        # 1. Explicit flags
        if self.auto_yes:
            return True
        if self.auto_no:
            return False

        # 2. Env override
        env = os.getenv("AGENTER_YES")
        if env == "1":
            return True
        if env == "0":
            return False

        # 3. Pre-supplied answers queue
        if self.confirms_queue:
            return bool(self.confirms_queue.pop(0))

        # 4. If answers were loaded but queue is exhausted → use default silently
        if self._has_answers:
            return default

        return None  # → caller must try TTY

    def resolve_prompt(self, key: str, default: Any) -> Any:
        """
        Resolve a ``prompt()`` call without blocking on TTY.

        Returns the resolved value, or ``None`` if the human must be asked.

        Args:
            key:     Slugified prompt text (or explicit ``param_name``).
            default: Default value provided by the developer.
        """
        # 1. Named answer
        if key in self.prompts_dict:
            return self.prompts_dict[key]

        # 2. Positional answer queue
        if self.prompts_queue:
            return self.prompts_queue.pop(0)

        # 3. Default value
        if default is not None:
            return default

        return None  # → caller must try TTY

    def resolve_edit(self, current_text: str) -> str | None:
        """Resolve an ``edit()`` call from the answers dict or piped STDIN."""
        if "edit" in self.prompts_dict:
            return str(self.prompts_dict["edit"])
        if self.prompts_queue:
            return str(self.prompts_queue.pop(0))
        if self._has_answers:
            return current_text  # fall back to current content silently
        return None  # → caller must try $EDITOR


_local = threading.local()


def get_session() -> InteractiveSession:
    """Return the InteractiveSession for the current thread, creating one if absent."""
    if not hasattr(_local, "session"):
        _local.session = InteractiveSession()
    return _local.session


def set_session(session: InteractiveSession) -> None:
    """Set the active InteractiveSession for the current thread."""
    _local.session = session
