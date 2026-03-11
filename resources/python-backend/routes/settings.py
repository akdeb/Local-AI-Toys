"""Settings, health, network, and event-stream endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db_service

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/startup-status")
async def startup_status():
    voices_n = db_service.db_service.get_table_count("voices")
    personalities_n = db_service.db_service.get_table_count("personalities")
    seeded = bool(getattr(db_service.db_service, "seeded_ok", False))
    return {
        "ready": bool(seeded),
        "seeded": bool(seeded),
        "pipeline_ready": False,
        "counts": {"voices": voices_n, "personalities": personalities_n},
    }


@router.get("/settings")
async def get_all_settings():
    return db_service.db_service.get_all_settings()


@router.get("/settings/{key}")
async def get_setting(key: str):
    value = db_service.db_service.get_setting(key)
    return {"key": key, "value": value}


class SettingUpdate(BaseModel):
    value: Optional[str] = None


@router.put("/settings/{key}")
async def set_setting(key: str, body: SettingUpdate):
    from utils import normalize_tts_backend

    if key == "tts_backend":
        try:
            normalized = normalize_tts_backend(body.value)
            db_service.db_service.set_setting(key, normalized)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"key": key, "value": normalized}
    db_service.db_service.set_setting(key, body.value)
    return {"key": key, "value": body.value}


@router.delete("/settings/{key}")
async def delete_setting(key: str):
    success = db_service.db_service.delete_setting(key)
    return {"deleted": success}


# --- Active user ---


class ActiveUserUpdate(BaseModel):
    user_id: Optional[str] = None


@router.get("/active-user")
async def get_active_user():
    user_id = db_service.db_service.get_active_user_id()
    user = db_service.db_service.get_user(user_id) if user_id else None
    return {
        "user_id": user_id,
        "user": {
            "id": user.id,
            "name": user.name,
            "current_personality_id": user.current_personality_id,
        }
        if user
        else None,
    }


@router.put("/active-user")
async def set_active_user(body: ActiveUserUpdate):
    db_service.db_service.set_active_user_id(body.user_id)
    return await get_active_user()


# --- App mode ---


class AppModeUpdate(BaseModel):
    mode: str


@router.get("/app-mode")
async def get_app_mode():
    return {"mode": db_service.db_service.get_app_mode()}


@router.put("/app-mode")
async def set_app_mode(body: AppModeUpdate):
    mode = db_service.db_service.set_app_mode(body.mode)
    return {"mode": mode}
