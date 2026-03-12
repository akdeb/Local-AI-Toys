import os
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

VOICE_TRANSCRIPT_CACHE: dict[str, str] | None = None
logger = logging.getLogger(__name__)

VOICES_JSON_URL = "https://raw.githubusercontent.com/akdeb/open-toys/main/app/src/assets/voices.json"


def _default_app_data_dir() -> Path:
    db_path = os.environ.get("ELATO_DB_PATH")
    if db_path:
        return Path(db_path).expanduser().resolve().parent
    try:
        from db.paths import default_db_path
        return Path(default_db_path()).expanduser().resolve().parent
    except Exception:
        return Path.cwd()


def _voices_dir() -> Path:
    return Path(os.environ.get("ELATO_VOICES_DIR") or _default_app_data_dir().joinpath("voices"))


def resolve_voice_ref_audio_path(voice_id: Optional[str]) -> Optional[str]:
    if not voice_id:
        return None
    try:
        path = _voices_dir().joinpath(f"{voice_id}.wav")
        if path.exists() and path.is_file():
            return str(path)
    except Exception:
        return None
    return None


def resolve_voice_ref_text(voice_id: Optional[str]) -> Optional[str]:
    if not voice_id:
        return None

    # Deterministic source of truth: DB (covers both global and user-created voices).
    try:
        import db_service

        v = db_service.db_service.get_voice(voice_id)
        t = (getattr(v, "transcript", None) or "").strip() if v else ""
        if t:
            return t
    except Exception:
        pass

    global VOICE_TRANSCRIPT_CACHE
    if VOICE_TRANSCRIPT_CACHE is None:
        VOICE_TRANSCRIPT_CACHE = {}
        try:
            # Primary source: canonical voices.json in GitHub.
            with urllib.request.urlopen(VOICES_JSON_URL, timeout=15) as response:
                if response.status == 200:
                    payload = json.loads(response.read().decode("utf-8"))
                    if isinstance(payload, list):
                        for item in payload:
                            if not isinstance(item, dict):
                                continue
                            vid = str(item.get("voice_id") or "").strip()
                            transcript = str(item.get("transcript") or "").strip()
                            if vid and transcript:
                                VOICE_TRANSCRIPT_CACHE[vid] = transcript
        except Exception as e:
            logger.warning("Failed to load voice transcripts from GitHub: %s", e)

        if not VOICE_TRANSCRIPT_CACHE:
            # Dev fallback: local asset file.
            try:
                repo_root = Path(__file__).resolve().parents[3]
                voices_json = repo_root / "app" / "src" / "assets" / "voices.json"
                if voices_json.exists():
                    payload = json.loads(voices_json.read_text(encoding="utf-8"))
                    if isinstance(payload, list):
                        for item in payload:
                            if not isinstance(item, dict):
                                continue
                            vid = str(item.get("voice_id") or "").strip()
                            transcript = str(item.get("transcript") or "").strip()
                            if vid and transcript:
                                VOICE_TRANSCRIPT_CACHE[vid] = transcript
            except Exception:
                pass

    return VOICE_TRANSCRIPT_CACHE.get(voice_id)
