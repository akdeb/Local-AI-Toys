
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class EngineRuntimeContext:
    now: datetime
    time_of_day: str
    day_of_week: str
    local_date: str
    local_time: str


def _time_of_day(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 21:
        return "evening"
    return "night"


def build_runtime_context(now: Optional[datetime] = None) -> EngineRuntimeContext:
    dt = now or datetime.now().astimezone()
    return EngineRuntimeContext(
        now=dt,
        time_of_day=_time_of_day(dt),
        day_of_week=dt.strftime("%A"),
        local_date=dt.strftime("%Y-%m-%d"),
        local_time=dt.strftime("%H:%M"),
    )


def build_system_prompt(
    *,
    personality_name: Optional[str],
    personality_prompt: Optional[str],
    user_context: Optional[Dict[str, Any]],
    runtime: EngineRuntimeContext,
    extra_system_prompt: Optional[str] = None,
) -> str:
    name = personality_name or "Assistant"
    base = (personality_prompt or "").strip()
    extra = (extra_system_prompt or "").strip()

    user_block = ""
    if user_context:
        parts: List[str] = []
        for k, v in user_context.items():
            if v is None:
                continue
            if isinstance(v, list):
                if not v:
                    continue
                vv = ", ".join(str(x) for x in v)
            else:
                vv = str(v)
            parts.append(f"{k}: {vv}")
        if parts:
            user_block = "\n".join(parts)

    prompt_parts: List[str] = []
    if base:
        prompt_parts.append(base)
    if extra:
        prompt_parts.append(extra)

    prompt_parts.append(
        "\n".join(
            [
                f"You are {name}.",
                f"It is {runtime.time_of_day} on {runtime.day_of_week}.",
                f"Local date: {runtime.local_date}.",
                f"Local time: {runtime.local_time}.",
            ]
        )
    )

    if user_block:
        prompt_parts.append("User context:\n" + user_block)

    return "\n\n".join(p.strip() for p in prompt_parts if p and p.strip())


def build_llm_messages(
    *,
    system_prompt: str,
    history: List[Dict[str, str]],
    user_text: str,
    max_history_messages: int = 30,
) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    expected_role = "user"
    if history:
        trimmed = history[-max_history_messages:]
        for m in trimmed:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if not role or not content:
                continue
            if role not in ("user", "assistant"):
                continue
            # Normalize to strict alternation required by some chat templates/models.
            # We intentionally drop out-of-order turns (e.g. leading assistant greeting).
            if role != expected_role:
                continue
            msgs.append({"role": role, "content": content})
            expected_role = "assistant" if expected_role == "user" else "user"

    # Never send consecutive user turns to the model. If history currently ends
    # on a user turn (common right after STT is persisted), drop trailing user
    # entries and treat this request as the current user input.
    while len(msgs) > 1 and msgs[-1].get("role") == "user":
        msgs.pop()

    msgs.append({"role": "user", "content": user_text})
    return msgs
