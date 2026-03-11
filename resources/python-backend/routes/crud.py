"""Users, Experiences/Personalities, Conversations, Sessions, and shutdown endpoints."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db_service
from engine.prompts import experience_generation_prompts

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users")
async def get_users():
    users = db_service.db_service.get_users()
    return [
        {
            "id": u.id, "name": u.name, "age": u.age,
            "current_personality_id": u.current_personality_id,
            "user_type": u.user_type,
            "about_you": getattr(u, "about_you", "") or "",
            "avatar_emoji": getattr(u, "avatar_emoji", None),
        }
        for u in users
    ]


class UserCreate(BaseModel):
    name: str
    age: Optional[int] = None
    about_you: Optional[str] = ""
    avatar_emoji: Optional[str] = None


@router.post("/users")
async def create_user(body: UserCreate):
    user = db_service.db_service.create_user(
        name=body.name, age=body.age,
        about_you=body.about_you or "",
        avatar_emoji=body.avatar_emoji,
    )
    return {"id": user.id, "name": user.name}


@router.put("/users/{user_id}")
async def update_user(user_id: str, body: Dict[str, Any]):
    user = db_service.db_service.update_user(user_id, **body)
    if not user:
        return {"error": "User not found"}, 404
    return {"id": user.id, "name": user.name}


# ---------------------------------------------------------------------------
# Experiences (personalities, games, stories)
# ---------------------------------------------------------------------------

def _experience_to_dict(p) -> dict:
    return {
        "id": p.id, "name": p.name, "prompt": p.prompt,
        "short_description": p.short_description, "tags": p.tags,
        "is_visible": p.is_visible, "is_global": p.is_global,
        "voice_id": p.voice_id,
        "type": getattr(p, "type", "personality"),
        "img_src": getattr(p, "img_src", None),
        "created_at": getattr(p, "created_at", None),
    }


@router.get("/experiences")
async def get_experiences(include_hidden: bool = False, type: Optional[str] = None):
    experiences = db_service.db_service.get_experiences(
        include_hidden=include_hidden,
        experience_type=type if type in ("personality", "game", "story") else None,
    )
    return [_experience_to_dict(p) for p in experiences]


@router.get("/personalities")
async def get_personalities(include_hidden: bool = False):
    personalities = db_service.db_service.get_experiences(
        include_hidden=include_hidden, experience_type=None,
    )
    return [_experience_to_dict(p) for p in personalities]


class ExperienceCreate(BaseModel):
    name: str
    prompt: str
    short_description: Optional[str] = ""
    tags: list = []
    voice_id: str = "radio"
    type: str = "personality"
    is_global: bool = False
    img_src: Optional[str] = None


PersonalityCreate = ExperienceCreate


@router.post("/experiences")
async def create_experience(body: ExperienceCreate):
    exp_type = body.type if body.type in ("personality", "game", "story") else "personality"
    p = db_service.db_service.create_experience(
        name=body.name, prompt=body.prompt,
        short_description=body.short_description or "",
        tags=body.tags, voice_id=body.voice_id,
        experience_type=exp_type, is_global=False, img_src=body.img_src,
    )
    return _experience_to_dict(p)


@router.post("/personalities")
async def create_personality(body: ExperienceCreate):
    p = db_service.db_service.create_experience(
        name=body.name, prompt=body.prompt,
        short_description=body.short_description or "",
        tags=body.tags, voice_id=body.voice_id,
        experience_type="personality", is_global=False, img_src=body.img_src,
    )
    return {"id": p.id, "name": p.name}


class GenerateExperienceRequest(BaseModel):
    description: str
    voice_id: Optional[str] = None
    type: str = "personality"


GeneratePersonalityRequest = GenerateExperienceRequest


@router.post("/experiences/generate")
async def generate_experience(request: Request, body: GenerateExperienceRequest):
    pipeline = getattr(request.app.state, "pipeline", None)
    if not pipeline:
        raise HTTPException(status_code=503, detail="AI engine not ready")

    exp_type = body.type if body.type in ("personality", "game", "story") else "personality"
    voice_id = body.voice_id or "radio"
    prompts = experience_generation_prompts(body.description, exp_type)

    name = await pipeline.generate_text_simple(prompts["name"], max_tokens=30)
    name = name.strip().strip('"').strip("'").split("\n")[0]

    short_desc = await pipeline.generate_text_simple(prompts["description"], max_tokens=100)
    short_desc = short_desc.strip().strip('"').strip("'")

    system_prompt = await pipeline.generate_text_simple(prompts["system"], max_tokens=300)
    system_prompt = system_prompt.strip()

    p = db_service.db_service.create_experience(
        name=name, prompt=system_prompt, short_description=short_desc,
        tags=[], voice_id=voice_id, experience_type=exp_type, is_global=False,
    )
    return _experience_to_dict(p)


@router.post("/personalities/generate")
async def generate_personality(request: Request, body: GenerateExperienceRequest):
    body.type = "personality"
    return await generate_experience(request, body)


@router.put("/experiences/{experience_id}")
async def update_experience(experience_id: str, body: Dict[str, Any]):
    p = db_service.db_service.update_experience(experience_id, **body)
    if not p:
        raise HTTPException(status_code=404, detail="Experience not found")
    return _experience_to_dict(p)


@router.put("/personalities/{personality_id}")
async def update_personality(personality_id: str, body: Dict[str, Any]):
    p = db_service.db_service.update_experience(personality_id, **body)
    if not p:
        return {"error": "Personality not found"}, 404
    return {"id": p.id, "name": p.name}


@router.delete("/experiences/{experience_id}")
async def delete_experience(experience_id: str):
    ok = db_service.db_service.delete_experience(experience_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Experience not found or cannot delete global")
    return {"ok": True}


@router.delete("/personalities/{personality_id}")
async def delete_personality(personality_id: str):
    ok = db_service.db_service.delete_experience(personality_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Personality not found or cannot delete global")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Conversations & Sessions
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def get_conversations(limit: int = 50, offset: int = 0, session_id: Optional[str] = None):
    convos = db_service.db_service.get_conversations(limit=limit, offset=offset, session_id=session_id)
    return [
        {"id": c.id, "role": c.role, "transcript": c.transcript, "timestamp": c.timestamp, "session_id": c.session_id}
        for c in convos
    ]


@router.get("/sessions")
async def get_sessions(limit: int = 50, offset: int = 0, user_id: Optional[str] = None):
    sessions = db_service.db_service.get_sessions(limit=limit, offset=offset, user_id=user_id)
    return [
        {"id": s.id, "started_at": s.started_at, "ended_at": s.ended_at,
         "duration_sec": s.duration_sec, "client_type": s.client_type,
         "user_id": s.user_id, "personality_id": s.personality_id}
        for s in sessions
    ]


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

@router.post("/shutdown")
async def shutdown():
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "shutting down"}
