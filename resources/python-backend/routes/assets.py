"""Voice and image asset management endpoints."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _app_data_dir() -> Path:
    db_path = os.environ.get("ELATO_DB_PATH")
    if db_path:
        return Path(db_path).expanduser().resolve().parent
    try:
        from db.paths import default_db_path
        return Path(default_db_path()).expanduser().resolve().parent
    except Exception:
        return Path.cwd()


def _voices_dir() -> Path:
    return Path(os.environ.get("ELATO_VOICES_DIR") or _app_data_dir().joinpath("voices"))


def _images_dir() -> Path:
    return Path(os.environ.get("ELATO_IMAGES_DIR") or _app_data_dir().joinpath("images"))


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------

@router.get("/voices")
async def get_voices(include_non_global: bool = True):
    voices = db_service.db_service.get_voices(include_non_global=include_non_global)
    return [
        {
            "voice_id": v.voice_id,
            "gender": v.gender,
            "voice_name": v.voice_name,
            "voice_description": v.voice_description,
            "voice_src": v.voice_src,
            "is_global": v.is_global,
            "created_at": getattr(v, "created_at", None),
        }
        for v in voices
    ]


class VoiceCreate(BaseModel):
    voice_id: str
    voice_name: str
    voice_description: Optional[str] = None


@router.post("/voices")
async def create_voice(body: VoiceCreate):
    v = db_service.db_service.upsert_voice(
        voice_id=body.voice_id, voice_name=body.voice_name,
        voice_description=body.voice_description, gender=None,
        voice_src=None, is_global=False,
    )
    if not v:
        raise HTTPException(status_code=500, detail="Failed to create voice")
    return {
        "voice_id": v.voice_id, "gender": v.gender,
        "voice_name": v.voice_name, "voice_description": v.voice_description,
        "voice_src": v.voice_src, "is_global": v.is_global,
        "created_at": getattr(v, "created_at", None),
    }


# ---------------------------------------------------------------------------
# Voice asset download / list / base64
# ---------------------------------------------------------------------------

class VoiceDownloadRequest(BaseModel):
    voice_id: str


@router.post("/assets/voices/download")
async def download_voice_asset(body: VoiceDownloadRequest):
    voice_id = (body.voice_id or "").strip()
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")

    base_url = os.environ.get(
        "ELATO_VOICE_BASE_URL",
        "https://pub-6b92949063b142d59fc3478c56ec196c.r2.dev",
    ).rstrip("/")
    url = f"{base_url}/{urllib.parse.quote(voice_id)}.wav"
    timeout_s = float(os.environ.get("ELATO_VOICE_TIMEOUT_S", "10"))

    out_dir = _voices_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = out_dir / f"{voice_id}.wav.part"
    final_path = out_dir / f"{voice_id}.wav"

    try:
        def _fetch() -> None:
            try:
                start = time.monotonic()
                bytes_written = 0
                use_proxy = os.environ.get("ELATO_VOICE_USE_PROXY", "0") == "1"
                opener = (
                    urllib.request.build_opener()
                    if use_proxy
                    else urllib.request.build_opener(urllib.request.ProxyHandler({}))
                )
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Elato/1.0",
                    "Accept": "audio/wav,application/octet-stream;q=0.9,*/*;q=0.8",
                    "Accept-Encoding": "identity",
                })
                with opener.open(req, timeout=timeout_s) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=404, detail=f"Voice not found: {voice_id}")
                    with open(tmp_path, "wb") as f:
                        while True:
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            bytes_written += len(chunk)
                logger.info("Downloaded voice %s (%d bytes) in %.2fs", voice_id, bytes_written, time.monotonic() - start)
                if tmp_path.exists():
                    tmp_path.replace(final_path)
            except Exception:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise

        await asyncio.to_thread(_fetch)
    except HTTPException:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise HTTPException(status_code=404, detail=f"Voice not found: {voice_id}")
        raise HTTPException(status_code=502, detail=f"Failed to download (HTTP {e.code})")
    except (socket.timeout, TimeoutError):
        raise HTTPException(status_code=504, detail=f"Timeout after {timeout_s:.0f}s")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            raise HTTPException(status_code=504, detail=f"Timeout after {timeout_s:.0f}s")
        raise HTTPException(status_code=502, detail=f"Network error: {getattr(e, 'reason', e)}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to download: {e}")

    return {"path": str(final_path)}


@router.get("/assets/voices/list")
async def list_downloaded_voices():
    out_dir = _voices_dir()
    if not out_dir.exists():
        return {"voices": []}
    voices = sorted(p.stem for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".wav")
    return {"voices": voices}


@router.get("/assets/voices/{voice_id}/base64")
async def read_voice_base64(voice_id: str):
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return {"base64": None}
    path = _voices_dir() / f"{voice_id}.wav"
    if not path.exists() or not path.is_file():
        return {"base64": None}
    return {"base64": base64.b64encode(path.read_bytes()).decode("utf-8")}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class ImageSaveRequest(BaseModel):
    experience_id: str
    base64_image: str
    ext: Optional[str] = None


@router.post("/assets/images/save")
async def save_experience_image(body: ImageSaveRequest):
    exp_id = (body.experience_id or "").strip()
    if not exp_id:
        raise HTTPException(status_code=400, detail="experience_id is required")
    try:
        data = base64.b64decode(body.base64_image or "")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to decode base64: {e}")

    safe_ext = "".join(c for c in (body.ext or "png").lower() if c.isalnum()) or "png"
    out_dir = _images_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"personality_{exp_id}.{safe_ext}"
    path.write_bytes(data)
    return {"path": str(path)}
