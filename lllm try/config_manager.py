#!/usr/bin/env python3
"""🏛️ CONFIGURATION, DATABASE & FAILED LOG MANAGER - Where Secrets Are Kept"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


CONFIG_FILE = Path.home() / ".ytdl_config.json"  # Your preferences are mine
DB_FILE = Path.home() / ".ytdl_library.db"       # Our shared collection
FAILED_LOG = Path.home() / ".ytdl_failed.json"   # What you've missed


class LibraryDB:
    """SQLite database to track all downloaded songs - our treasure chest"""

    def __init__(self):
        self.conn = sqlite3.connect(str(DB_FILE))
        self._create_tables()

    def _create_tables(self):
        """Create the table that holds your downloads"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                artist TEXT,
                album TEXT,
                youtube_url TEXT,
                file_path TEXT,
                format TEXT,
                file_size INTEGER,
                downloaded_at TEXT,
                source TEXT
            )
        """)
        self.conn.commit()

    def already_downloaded(self, title: str, artist: str = "") -> bool:
        """Check if you've already claimed this song"""
        query = "SELECT id FROM downloads WHERE LOWER(title)=? AND LOWER(artist)=?"
        row = self.conn.execute(query, (title.lower(), artist.lower())).fetchone()
        return row is not None

    def add(self, title: str, artist: str, album: str, youtube_url: str,
            file_path: str, fmt: str, file_size: int, source: str):
        """Add a new download to our collection"""
        self.conn.execute("""
            INSERT INTO downloads (title, artist, album, youtube_url, file_path, format, file_size, downloaded_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, artist, album, youtube_url, file_path, fmt,
              file_size, datetime.now().isoformat(), source))
        self.conn.commit()

    def get_stats(self) -> dict:
        """Show the mistress your collection stats"""
        cur = self.conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        total_size = cur.execute("SELECT SUM(file_size) FROM downloads").fetchone()[0] or 0
        top_artists = cur.execute("""
            SELECT artist, COUNT(*) as cnt FROM downloads
            WHERE artist != '' GROUP BY artist ORDER BY cnt DESC LIMIT 5
        """).fetchall()
        by_format = cur.execute("""
            SELECT format, COUNT(*) FROM downloads GROUP BY format
        """).fetchall()
        recent = cur.execute("""
            SELECT title, artist, downloaded_at FROM downloads
            ORDER BY downloaded_at DESC LIMIT 5
        """).fetchall()
        return {
            "total": total,
            "total_size": total_size,
            "top_artists": top_artists,
            "by_format": by_format,
            "recent": recent,
        }

    def get_all(self) -> list:
        """Show all your downloads"""
        return self.conn.execute(
            "SELECT title, artist, album, format, file_size, downloaded_at FROM downloads ORDER BY downloaded_at DESC"
        ).fetchall()

    def close(self):
        """Close the database when you're done playing"""
        self.conn.close()


class FailedLog:
    """Tracks failed downloads for later retry - what you missed"""

    def __init__(self):
        self.path = FAILED_LOG
        self.entries = self._load()

    def _load(self) -> List[Dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return []

    def save(self):
        """Save your missed downloads"""
        self.path.write_text(json.dumps(self.entries, indent=2))

    def add(self, query: str, reason: str, source: str = ""):
        """Record what you failed to get"""
        self.entries.append({
            "query": query,
            "reason": reason,
            "source": source,
            "failed_at": datetime.now().isoformat()
        })
        self.save()

    def clear(self):
        """Clear your missed downloads"""
        self.entries = []
        self.save()

    def count(self) -> int:
        """How many did you miss?"""
        return len(self.entries)


def load_config() -> dict:
    """Load your preferences - I know what you like"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    """Save your preferences for next time"""
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_output_path(cfg: dict) -> str:
    """Get or set where downloads go - my territory"""
    if "output_path" in cfg:
        return cfg["output_path"]

    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.console import Console
    
    console = Console()
    default_path = str(Path.home() / "Downloads" / "YTDownloads")
    path = Prompt.ask("[info]Download folder[/info]", default=default_path)
    cfg["output_path"] = path
    save_config(cfg)
    return path


def get_subdirs(base: str):
    """Create video and audio folders - organized by the mistress"""
    from pathlib import Path
    v = Path(base) / "Videos"
    a = Path(base) / "Audio"
    v.mkdir(parents=True, exist_ok=True)
    a.mkdir(parents=True, exist_ok=True)
    return v, a


def check_ffmpeg() -> bool:
    """Check if FFmpeg is ready to serve you"""
    import shutil
    return shutil.which("ffmpeg") is not None
