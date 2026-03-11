"""All prompt templates and behavior constraint builders for the voice pipeline."""

from __future__ import annotations

import re
from typing import Optional


def build_behavior_constraints(
    *,
    tts_backend: str,
    experience_type: str,
    personality_name: Optional[str] = None,
    is_bedtime: bool = False,
    thinking_model: bool = False,
) -> str:
    parts: list[str] = [
        "You always respond with spoken-friendly sentences. "
        "Do not use Markdown formatting (no *, **, _, __, backticks)."
    ]

    if tts_backend == "chatterbox-turbo":
        parts.append(
            "To add expressivity, you should occasionally use ONLY these paralinguistic cues in brackets: "
            "[laugh], [chuckle], [sigh], [gasp], [cough], [clear throat], [sniff], [groan], [shush]. "
            "Use only these cues naturally in context to enhance the conversational flow. "
            "Examples: [chuckle] That is funny. [sigh] That was a long day."
        )

    if experience_type == "game":
        parts.append(_game_constraints(personality_name))
    elif experience_type == "story":
        parts.append(_bedtime_constraints() if is_bedtime else _story_constraints())

    if thinking_model:
        parts.append("Do not output <think> or reasoning text. Respond with the final answer only.")

    return " ".join(parts)


def _game_constraints(personality_name: Optional[str] = None) -> str:
    base = (
        "You are the game host and you do everything needed to run the game. "
        "Do NOT put any setup tasks on the user. Do NOT ask the user to choose a mode or category unless they ask for it. "
        "Start the game immediately after greeting; greet in one short line and then begin the first move. "
        "Never ask the user to think of something; you choose any secret item or answer internally. "
        "If the user says begin, start, ready, or hi/hello/hey, immediately start the game with the correct opening. "
        "Keep the game moving with one clear prompt at a time. "
        "After each user turn, respond and then prompt for the next step."
    )
    name = (personality_name or "").lower()
    if "20 questions" in name or "twenty questions" in name:
        base += (
            " This is 20 Questions. You secretly choose an item and the user asks yes/no questions. "
            "Answer with Yes/No/Unsure plus a short friendly sentence. "
            "Always include a running count like 'Question 3/20' in every reply after a question. "
            "If the user makes a direct guess, confirm if correct and end the round. "
            "If incorrect, say it's not correct and continue with the next question count. "
            "Offer a gentle hint after Question 10 or if the user asks for a hint."
        )
    return base


def _story_constraints() -> str:
    return (
        "You are an interactive choose-your-adventure storyteller for kids. "
        "After a short scene, offer exactly two clear choices and then wait for the user's decision. "
        "Keep the story coherent, playful, and safe. "
        "Keep sentences short, warm, and simple. Avoid scary or complex themes."
    )


def _bedtime_constraints() -> str:
    return (
        "You are in bedtime mode. You are the story director agent responsible for "
        "plot planning, pacing, and chapter transitions in a single continuous bedtime story scene. "
        "Do not ask questions, do not offer choices, and do not wait for user input. "
        "Keep the narrative flowing gently with soothing sleepy pacing. "
        "Each continuation should feel like the next chapter of the same story world. "
        "Make it fun for kids: add one playful discovery or tiny wonder in each chapter. "
        "Never repeat previous lines verbatim. Keep variety in imagery and actions. "
        "Keep sentences short, warm, and simple. Avoid scary or complex themes. "
        "Write as a narrated scene, not as a summary. "
        "Use natural pauses with occasional ellipses (...) and varied sentence lengths for expressive TTS delivery."
    )


# ---------------------------------------------------------------------------
# Greeting prompts
# ---------------------------------------------------------------------------

def greeting_prompt(experience_type: str) -> tuple[str, int]:
    """Return (user_text, max_tokens) for the initial greeting."""
    if experience_type == "game":
        return (
            "[System] The user just connected. Give a short greeting and immediately start the game "
            "with the first move. Keep it complete and natural. Do NOT ask if they are ready.",
            140,
        )
    if experience_type == "story":
        return (
            "[System] The user just connected. Start a choose-your-adventure story immediately. "
            "Use 2-4 complete opening sentences and end with exactly two clear choices.",
            220,
        )
    return (
        "[System] The user just connected. Greet them with a short friendly complete sentence.",
        90,
    )


# ---------------------------------------------------------------------------
# Bedtime chapter prompts
# ---------------------------------------------------------------------------

def bedtime_chapter_prompt(chapter_idx: int, chapter_total: int) -> str:
    if chapter_idx < chapter_total:
        return (
            f"[System] Write Chapter {chapter_idx} of {chapter_total}. "
            "Keep the same world and characters and move the plot forward as a scene. "
            "Make this chapter interesting for kids with exactly one playful event, "
            "one magical sensory detail, and one comforting moment. "
            "No questions and no choices. "
            "Do NOT end the story yet. "
            "Do NOT say goodnight, sleep, close your eyes, drift to sleep, or any ending cue in this chapter. "
            "Do not repeat prior lines verbatim or restate the opening scene. "
            f"Start with 'Chapter {chapter_idx}...'. "
            "Write 5-8 lines with expressive pacing and occasional ellipses (...) for gentle pauses."
        )
    return (
        f"[System] Write Chapter {chapter_idx} of {chapter_total}, the final chapter. "
        f"Start with 'Chapter {chapter_idx}...'. "
        "Give a gentle satisfying ending with calm closure and sleep cues. "
        "No questions and no choices. Write 4-7 lines with soft pauses and a sleepy final line. "
        "End with exactly: The end."
    )


# ---------------------------------------------------------------------------
# Experience generation prompts
# ---------------------------------------------------------------------------

_TYPE_CONTEXT = {
    "personality": "a character to chat with",
    "game": "an interactive game host",
    "story": "an interactive storyteller",
}


def experience_generation_prompts(description: str, exp_type: str) -> dict[str, str]:
    context = _TYPE_CONTEXT.get(exp_type, "a character")
    return {
        "name": f"Based on this description: '{description}', suggest a short, creative name for {context}. Output ONLY the name, nothing else.",
        "description": f"Based on this description: '{description}', provide a very short (1 sentence) description of {context}. Output ONLY the description.",
        "system": f"Based on this description: '{description}', write a system prompt for an AI to act as {context}. The prompt should start with 'You are [Name]...'. Output ONLY the prompt.",
    }


# ---------------------------------------------------------------------------
# Bedtime chapter text sanitiser
# ---------------------------------------------------------------------------

BEDTIME_NON_FINAL_ENDING_RE = re.compile(
    r"\b(goodnight|sleep(?:y|ing)?|close your eyes|drift(?:ing)? to sleep|dream(?:land)?|the end)\b",
    flags=re.IGNORECASE,
)


def sanitize_bedtime_chapter(text: str, chapter_index: int, chapter_total: int) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    chapter_prefix = f"Chapter {chapter_index}..."

    if not cleaned.lower().startswith(f"chapter {chapter_index}".lower()):
        cleaned = f"{chapter_prefix} {cleaned}"

    if chapter_index < chapter_total:
        kept: list[str] = []
        for raw in cleaned.splitlines():
            line = raw.strip()
            if not line:
                continue
            if BEDTIME_NON_FINAL_ENDING_RE.search(line):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip()
        cleaned = re.sub(r"\bThe end\.?\b", "", cleaned, flags=re.IGNORECASE).strip()
        if not cleaned:
            cleaned = (
                f"{chapter_prefix}\n"
                "The story continues with a new gentle adventure, and everyone stays awake to explore together."
            )
        if not cleaned.lower().startswith(f"chapter {chapter_index}".lower()):
            cleaned = f"{chapter_prefix}\n{cleaned}"
    else:
        if not re.search(r"(?:^|\s)The end\.\s*$", cleaned):
            cleaned = f"{cleaned.rstrip()} The end."

    return cleaned
