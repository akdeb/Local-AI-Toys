import json
import logging
import time
import uuid
import urllib.error
import urllib.request
from typing import List, Optional

from .paths import assets_dir

logger = logging.getLogger(__name__)

ASSETS_BASE_URL = (
    "https://raw.githubusercontent.com/akdeb/open-toys/main/app/src/assets"
)
VOICES_URL = f"{ASSETS_BASE_URL}/voices.json"
EXPERIENCE_URLS = {
    "personalities.json": f"{ASSETS_BASE_URL}/personalities.json",
    "games.json": f"{ASSETS_BASE_URL}/games.json",
    "stories.json": f"{ASSETS_BASE_URL}/stories.json",
}


def _load_json_from_url(url: str) -> Optional[object]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            if response.status != 200:
                logger.warning("Asset fetch failed (%s): HTTP %s", url, response.status)
                return None
            data = response.read().decode("utf-8")
            return json.loads(data)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("Asset fetch failed (%s): %s", url, exc)
        return None


def _load_json_with_fallback(filename: str, url: str) -> Optional[object]:
    payload = _load_json_from_url(url)
    if payload is not None:
        return payload

    # Dev/local fallback only.
    local_path = assets_dir() / filename
    if not local_path.exists():
        return None
    try:
        return json.loads(local_path.read_text(encoding="utf-8"))
    except Exception:
        return None


class SeedMixin:
    def sync_global_voices_and_experiences(self) -> None:
        """Sync voices and experiences (personalities, games, stories) from JSON assets."""
        voices_payload = _load_json_with_fallback("voices.json", VOICES_URL)
        if not isinstance(voices_payload, list):
            return

        conn = self._get_conn()
        cursor = conn.cursor()

        # Sync voices
        if isinstance(voices_payload, list):
            for item in voices_payload:
                if not isinstance(item, dict):
                    continue
                vid = item.get("voice_id") or item.get("id")
                vname = item.get("voice_name") or item.get("name")
                if not vid or not vname:
                    continue
                cursor.execute(
                    """
                    INSERT INTO voices (voice_id, gender, voice_name, voice_description, transcript, voice_src, is_global, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(voice_id) DO UPDATE SET
                      gender = excluded.gender,
                      voice_name = excluded.voice_name,
                      voice_description = excluded.voice_description,
                      transcript = excluded.transcript,
                      voice_src = excluded.voice_src,
                      is_global = excluded.is_global,
                      created_at = COALESCE(voices.created_at, excluded.created_at)
                    """,
                    (
                        str(vid),
                        item.get("gender"),
                        str(vname),
                        item.get("voice_description") or item.get("description"),
                        item.get("transcript"),
                        item.get("voice_src") or item.get("src"),
                        time.time(),
                    ),
                )

        # Sync experiences from multiple JSON files
        for filename, url in EXPERIENCE_URLS.items():
            payload = _load_json_with_fallback(filename, url)

            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, dict):
                    continue
                p_id = item.get("id")
                name = item.get("name")
                prompt = item.get("prompt")
                voice_id = item.get("voice_id")
                exp_type = item.get("type", "personality")
                if not p_id or not name or not prompt or not voice_id:
                    continue
                cursor.execute(
                    "SELECT 1 FROM voices WHERE voice_id = ? LIMIT 1",
                    (str(voice_id),),
                )
                if not cursor.fetchone():
                    logger.warning(f"Voice {voice_id} not found, skipping {p_id}")
                    continue
                cursor.execute(
                    """
                    INSERT INTO personalities (id, name, prompt, short_description, tags, is_visible, voice_id, is_global, img_src, type, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      name = excluded.name,
                      prompt = excluded.prompt,
                      short_description = excluded.short_description,
                      tags = excluded.tags,
                      is_visible = excluded.is_visible,
                      voice_id = excluded.voice_id,
                      is_global = excluded.is_global,
                      img_src = excluded.img_src,
                      type = excluded.type,
                      created_at = COALESCE(personalities.created_at, excluded.created_at)
                    """,
                    (
                        str(p_id),
                        str(name),
                        str(prompt),
                        str(item.get("short_description") or ""),
                        json.dumps(item.get("tags") or []),
                        str(voice_id),
                        str(item.get("img_src") or ""),
                        str(exp_type),
                        time.time(),
                    ),
                )

        conn.commit()
        conn.close()

    def sync_global_voices_and_personalities(self) -> None:
        """Backward-compatible alias."""
        self.sync_global_voices_and_experiences()

    def _seed_default_user(self) -> None:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(1) AS n FROM users")
        row = cursor.fetchone()
        if row and row["n"]:
            conn.close()
            return

        user_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO users (id, name, age, dob, hobbies, about_you, personality_type, likes, current_personality_id, user_type, avatar_emoji)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                "User",
                None,
                None,
                json.dumps([]),
                "",
                None,
                json.dumps([]),
                None,
                "family",
                "🙂",
            ),
        )
        conn.commit()
        conn.close()

        if not self.get_active_user_id():
            self.set_active_user_id(user_id)
