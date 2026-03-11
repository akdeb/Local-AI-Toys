from __future__ import annotations

from typing import Dict, List, Optional


def build_context_history(
    *,
    db_service,
    current_session_id: str,
    user_id: Optional[str],
    personality_id: Optional[str],
    max_history_messages: int = 80,
    max_prior_sessions: int = 6,
) -> List[Dict[str, str]]:
    """
    Build conversation history for prompt context.

    Priority:
    1) Current session messages
    2) Recent prior sessions for the same user + same experience
    """
    history_msgs: List[Dict[str, str]] = []

    def _append_convos(session_id: str) -> None:
        try:
            convos = db_service.get_conversations(session_id=session_id)
        except Exception:
            return

        for c in convos:
            transcript = (getattr(c, "transcript", "") or "").strip()
            if not transcript or transcript == "[connected]":
                continue

            role = getattr(c, "role", "")
            if role == "user":
                history_msgs.append({"role": "user", "content": transcript})
            elif role == "ai":
                history_msgs.append({"role": "assistant", "content": transcript})

    # Current session first.
    _append_convos(current_session_id)

    # Pull prior sessions for same user + same experience.
    if user_id:
        try:
            sessions = db_service.get_sessions(limit=120, user_id=user_id)
        except Exception:
            sessions = []

        prior_ids: List[str] = []
        for s in sessions:
            sid = getattr(s, "id", None)
            if not sid or sid == current_session_id:
                continue

            if personality_id and getattr(s, "personality_id", None) != personality_id:
                continue

            prior_ids.append(sid)
            if len(prior_ids) >= max_prior_sessions:
                break

        # get_sessions returns newest first. Reverse to keep chronology.
        for sid in reversed(prior_ids):
            _append_convos(sid)

    if len(history_msgs) > max_history_messages:
        history_msgs = history_msgs[-max_history_messages:]

    return history_msgs
