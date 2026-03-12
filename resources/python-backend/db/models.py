from dataclasses import dataclass
from typing import List, Literal, Optional

ExperienceType = Literal["personality", "game", "story"]


@dataclass
class Experience:
    """Base model for personalities, games, and stories."""
    id: str
    name: str
    prompt: str
    short_description: str
    tags: List[str]
    is_visible: bool
    is_global: bool
    voice_id: str
    type: ExperienceType = "personality"
    img_src: Optional[str] = None
    created_at: Optional[float] = None


# Alias for backward compatibility
Personality = Experience


@dataclass
class Voice:
    voice_id: str
    gender: Optional[str]
    voice_name: str
    voice_description: Optional[str]
    transcript: Optional[str]
    voice_src: Optional[str]
    is_global: bool
    created_at: Optional[float] = None


@dataclass
class Conversation:
    id: str
    role: str
    transcript: str
    timestamp: float
    session_id: Optional[str] = None


@dataclass
class User:
    id: str
    name: str
    age: Optional[int]
    dob: Optional[str]
    about_you: str
    personality_type: Optional[str]
    likes: List[str]
    current_personality_id: Optional[str]
    user_type: str = "family"
    avatar_emoji: Optional[str] = None


@dataclass
class Session:
    id: str
    started_at: float
    ended_at: Optional[float]
    duration_sec: Optional[float]
    client_type: str
    user_id: Optional[str]
    personality_id: Optional[str]
