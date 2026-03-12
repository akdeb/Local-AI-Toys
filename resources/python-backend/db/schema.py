from sqlite3 import Connection


def init_schema(conn: Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS app_state (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        CREATE TABLE IF NOT EXISTS voices (
          voice_id TEXT PRIMARY KEY,
          gender TEXT,
          voice_name TEXT NOT NULL,
          voice_description TEXT,
          transcript TEXT,
          voice_src TEXT,
          is_global BOOLEAN DEFAULT 0,
          created_at REAL
        );

        CREATE TABLE IF NOT EXISTS personalities (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          prompt TEXT NOT NULL,
          short_description TEXT,
          tags TEXT,
          is_visible BOOLEAN DEFAULT 1,
          voice_id TEXT NOT NULL,
          is_global BOOLEAN DEFAULT 0,
          img_src TEXT,
          type TEXT DEFAULT 'personality',
          created_at REAL
        );

        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          started_at REAL NOT NULL,
          ended_at REAL,
          duration_sec REAL,
          client_type TEXT NOT NULL,
          user_id TEXT,
          personality_id TEXT
        );

        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY,
          role TEXT NOT NULL,
          transcript TEXT NOT NULL,
          timestamp REAL NOT NULL,
          session_id TEXT
        );

        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          age INTEGER,
          dob TEXT,
          hobbies TEXT,
          about_you TEXT DEFAULT '',
          personality_type TEXT,
          likes TEXT,
          current_personality_id TEXT,
          user_type TEXT DEFAULT 'family',
          avatar_emoji TEXT,
          device_volume INTEGER DEFAULT 70,
          FOREIGN KEY (current_personality_id) REFERENCES personalities (id)
        );

        INSERT INTO app_state (key, value)
        VALUES ('laptop_volume', '70')
        ON CONFLICT(key) DO NOTHING;

        PRAGMA foreign_keys=ON;
        """
    )
