import time
from typing import List, Optional

from .models import Voice


class VoicesMixin:
    def _voice_exists(self, voice_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM voices WHERE voice_id = ? LIMIT 1", (voice_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists

    def _default_voice_id(self) -> Optional[str]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT voice_id FROM voices ORDER BY created_at ASC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        return row["voice_id"] if row else None

    def get_voices(self, include_non_global: bool = True) -> List[Voice]:
        conn = self._get_conn()
        cursor = conn.cursor()
        if include_non_global:
            cursor.execute("SELECT * FROM voices ORDER BY created_at DESC, rowid DESC")
        else:
            cursor.execute(
                "SELECT * FROM voices WHERE is_global = 1 ORDER BY created_at DESC, rowid DESC"
            )
        rows = cursor.fetchall()
        conn.close()
        return [
            Voice(
                voice_id=row["voice_id"],
                gender=row["gender"],
                voice_name=row["voice_name"],
                voice_description=row["voice_description"],
                transcript=row["transcript"],
                voice_src=row["voice_src"],
                is_global=bool(row["is_global"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def upsert_voice(
        self,
        voice_id: str,
        voice_name: str,
        gender: Optional[str] = None,
        voice_description: Optional[str] = None,
        transcript: Optional[str] = None,
        voice_src: Optional[str] = None,
        is_global: bool = False,
    ) -> Optional[Voice]:
        conn = self._get_conn()
        cursor = conn.cursor()
        created_at = time.time()
        cursor.execute(
            """
            INSERT INTO voices (voice_id, gender, voice_name, voice_description, transcript, voice_src, is_global, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                voice_id,
                gender,
                voice_name,
                voice_description,
                transcript,
                voice_src,
                bool(is_global),
                created_at,
            ),
        )
        conn.commit()
        conn.close()
        return self.get_voice(voice_id)

    def get_voice(self, voice_id: str) -> Optional[Voice]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM voices WHERE voice_id = ?", (voice_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return Voice(
            voice_id=row["voice_id"],
            gender=row["gender"],
            voice_name=row["voice_name"],
            voice_description=row["voice_description"],
            transcript=row["transcript"],
            voice_src=row["voice_src"],
            is_global=bool(row["is_global"]),
            created_at=row["created_at"],
        )
