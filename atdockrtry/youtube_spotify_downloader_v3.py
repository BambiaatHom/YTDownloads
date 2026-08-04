#!/usr/bin/env python3
"""
YouTube Downloader (Pro Version - yt-dlp Edition) + Spotify Integration
───────────────────────────────────────────────────────────────────────
Stable, robust, and high-performance downloading engine using yt-dlp.
"""

import json
import os
import re
import shutil
import subprocess
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple

# Core Engine: yt-dlp (The industry standard)
import yt_dlp

# Third-party imports for UI and Features
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn, MofNCompleteColumn
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ID3 tagging (optional)
try:
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
    from mutagen.oggvorbis import OggVorbis
    import urllib.request
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# Desktop notifications (optional)
try:
    from plyer import notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False

# Spotify integration (optional)
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

# ── Theme & Console ──────────────────────────────────────────────────────────
custom_theme = Theme({
    "info": "bold cyan",
    "warning": "bold magenta",
    "danger": "bold red",
    "success": "bold green",
    "title": "bold white on blue",
    "url": "underline cyan",
    "highlight": "bold yellow",
    "muted": "dim white",
    "chapter": "bold blue",
    "nav": "dim cyan",
})
console = Console(theme=custom_theme)

CONFIG_FILE = Path.home() / ".ytdl_config.json"
DB_FILE = Path.home() / ".ytdl_library.db"
FAILED_LOG = Path.home() / ".ytdl_failed.json"


# ── Library Database (Unchanged - Highly Stable) ─────────────────────────────

class LibraryDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_FILE))
        self._create_tables()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, artist TEXT, album TEXT, youtube_url TEXT,
                file_path TEXT, format TEXT, file_size INTEGER, downloaded_at TEXT, source TEXT
            )
        """)
        self.conn.commit()

    def already_downloaded(self, title: str, artist: str = "") -> bool:
        query = "SELECT id FROM downloads WHERE LOWER(title)=? AND LOWER(artist)=?"
        row = self.conn.execute(query, (title.lower(), artist.lower())).fetchone()
        return row is not None

    def add(self, title: str, artist: str, album: str, youtube_url: str,
            file_path: str, fmt: str, file_size: int, source: str):
        self.conn.execute("""
            INSERT INTO downloads (title, artist, album, youtube_url, file_path, format, file_size, downloaded_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, artist, album, youtube_url, file_path, fmt,
              file_size, datetime.now().isoformat(), source))
        self.conn.commit()

    def get_stats(self) -> dict:
        cur = self.conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        total_size = cur.execute("SELECT SUM(file_size) FROM downloads").fetchone()[0] or 0
        top_artists = cur.execute("SELECT artist, COUNT(*) as cnt FROM downloads WHERE artist != '' GROUP BY artist ORDER BY cnt DESC LIMIT 5").fetchall()
        by_format = cur.execute("SELECT format, COUNT(*) FROM downloads GROUP BY format").fetchall()
        recent = cur.execute("SELECT title, artist, downloaded_at FROM downloads ORDER BY downloaded_at DESC LIMIT 5").fetchall()
        return {"total": total, "total_size": total_size, "top_artists": top_artists, "by_format": by_format, "recent": recent}

    def close(self):
        self.conn.close()


# ── Failed Downloads Log ─────────────────────────────────────────────────────

class FailedLog:
    def __init__(self):
        self.path = FAILED_LOG
        self.entries = self._load()

    def _load(self) -> List[Dict]:
        if self.path.exists():
            try: return json.loads(self.path.read_text())
            except: pass
        return []

    def save(self):
        self.path.write_text(json.dumps(self.entries, indent=2))

    def add(self, query: str, reason: str, source: str = ""):
        self.entries.append({"query": query, "reason": reason, "source": source, "failed_at": datetime.now().isoformat()})
        self.save()

    def clear(self):
        self.entries = []
        self.save()

    def count(self) -> int:
        return len(self.entries)


# ── Desktop Notifications ────────────────────────────────────────────────────

def notify(title: str, message: str):
    if NOTIFICATIONS_AVAILABLE:
        try: notification.notify(title=title, message=message, app_name="YT Downloader", timeout=5)
        except: pass


# Since we need access to the 'progress' object, we'll define a simpler hook inside the download function.

def yt_dlp_progress_callback(progress_task, progress_obj):
    """The real magic: connects yt-dlint to Rich."""
    def hook(d):
        if d['status'] == 'downloading':
            try:
                # Parse percentage string (e.g., " 12.5%")
                p_str = d.get('_percent_str', '0%').split('%')[0].strip()
                percent = float(p_str)
                # Update the progress bar (we use 0-100 as the total for simplicity here)
                progress_obj.update(task_id, completed=percent)
            except: pass
        elif d['status'] == 'finished':
            progress_obj.update(task_id, completed=100)
    return hook


# ── Configuration & Setup (Unchanged) ─────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}

def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def get_output_path(cfg: dict) -> str:
    if "output_path" in cfg: return cfg["output_path"]
    default_path = str(Path.home() / "Downloads" / "YTDownloads")
    path = Prompt.ask("[info]Download folder[/info]", default=default_path)
    cfg["output_path"] = path
    save_config(cfg)
    return path

def check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

def get_subdirs(base: str) -> tuple[Path, Path]:
    v = Path(base) / "Videos"
    a = Path(base) / "Audio"
    v.mkdir(parents=True, exist_ok=True)
    a.mkdir(parents=True, exist_ok=True)
    return v, a

# ── Spotify Integration (Unchanged) ──────────────────────────────────────────
# [Note: I am omitting the full SpotifyManager class for brevity in this view, 
# but it remains EXACTLY as you had it in your original script.]
# [RE-INSERT YOUR ORIGINAL SpotifyManager CLASS HERE]
# ── Spotify Integration ──────────────────────────────────────────────────────

class SpotifyManager:
    """Handles all Spotify API interactions."""
    
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sp = None
        
    def setup_spotify(self) -> bool:
        """Set up Spotify authentication. Returns True if successful."""
        if not SPOTIFY_AVAILABLE:
            console.print("[danger]Spotify integration not available![/danger]")
            console.print("[info]Install spotipy: pip install spotipy[/info]")
            return False
            
        console.print(Panel(
            "[info]Spotify Setup[/info]\n"
            "You'll need Spotify API credentials:\n"
            "1. Go to https://developer.spotify.com/dashboard\n"
            "2. Create an app\n"
            "3. Get your Client ID and Client Secret\n"
            "4. Set redirect URI to: http://127.0.0.1:8118/callback",
            border_style="cyan"
        ))
        
        # Check if credentials are already saved
        if "spotify_client_id" in self.cfg and "spotify_client_secret" in self.cfg:
            use_saved = Confirm.ask("[info]Use saved Spotify credentials?[/info]", default=True)
            if use_saved:
                client_id = self.cfg["spotify_client_id"]
                client_secret = self.cfg["spotify_client_secret"]
            else:
                client_id = Prompt.ask("[info]Spotify Client ID[/info]")
                client_secret = Prompt.ask("[info]Spotify Client Secret[/info]", password=True)
                self.cfg["spotify_client_id"] = client_id
                self.cfg["spotify_client_secret"] = client_secret
                save_config(self.cfg)
        else:
            client_id = Prompt.ask("[info]Spotify Client ID[/info]")
            client_secret = Prompt.ask("[info]Spotify Client Secret[/info]", password=True)
            
            if Confirm.ask("[info]Save credentials for next time?[/info]", default=True):
                self.cfg["spotify_client_id"] = client_id
                self.cfg["spotify_client_secret"] = client_secret
                save_config(self.cfg)
        
        # Ask if they want to clear cache and re-authenticate
        cache_path = Path.home() / ".cache"
        spotify_cache_files = list(cache_path.glob("spotify-*")) if cache_path.exists() else []
        
        if spotify_cache_files:
            console.print(f"\n[warning]Found existing Spotify authentication cache[/warning]")
            if Confirm.ask("[info]Clear cache and re-authenticate? (Recommended if you're having permission issues)[/info]", default=False):
                for cache_file in spotify_cache_files:
                    try:
                        cache_file.unlink()
                        console.print(f"[success]Deleted: {cache_file.name}[/success]")
                    except Exception as e:
                        console.print(f"[warning]Could not delete {cache_file.name}: {e}[/warning]")
        
        try:
            scope = "user-library-read playlist-read-private playlist-read-collaborative user-top-read user-follow-read"
            
            # Create auth manager with cache settings
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri="http://127.0.0.1:8118/callback",
                scope=scope,
                open_browser=True
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            
            # Test the connection
            user = self.sp.current_user()
            console.print(f"[success]✔ Connected to Spotify as: {user['display_name']}[/success]")
            
            # Show what permissions we have
            try:
                token_info = auth_manager.get_cached_token()
                if token_info and 'scope' in token_info:
                    console.print(f"[info]Current permissions: {token_info['scope']}[/info]\n")
                else:
                    console.print("[warning]Could not verify permissions[/warning]\n")
            except:
                pass
            
            return True
            
        except Exception as e:
            console.print(f"[danger]Failed to connect to Spotify: {e}[/danger]")
            console.print("\n[info]Troubleshooting:[/info]")
            console.print("1. Make sure your Client ID and Secret are correct")
            console.print("2. Verify redirect URI is set to: http://127.0.0.1:8118/callback")
            console.print("3. Try clearing cache and re-authenticating")
            return False
    
    def get_liked_songs(self, limit: int = 50) -> List[Dict]:
        """Fetch user's liked songs from Spotify."""
        if not self.sp:
            return []
        
        songs = []
        with console.status("[info]Fetching liked songs from Spotify...[/info]"):
            offset = 0
            batch_size = 50  # Spotify API limit per request
            
            while True:
                results = self.sp.current_user_saved_tracks(limit=batch_size, offset=offset)
                
                if not results['items']:
                    break
                
                for item in results['items']:
                    track = item['track']
                    songs.append({
                        'name': track['name'],
                        'artist': track['artists'][0]['name'],
                        'album': track['album']['name'],
                        'search_query': f"{track['artists'][0]['name']} - {track['name']}"
                    })
                
                # Check if we've reached the limit or there are no more results
                if len(songs) >= limit or not results['next']:
                    break
                
                offset += batch_size
        
        return songs[:limit] if limit < 999999 else songs
    
    def get_playlists(self) -> List[Dict]:
        """Fetch user's playlists."""
        if not self.sp:
            return []
        
        playlists = []
        with console.status("[info]Fetching playlists...[/info]"):
            results = self.sp.current_user_playlists()
            for item in results['items']:
                if item:  # Check item exists
                    playlists.append({
                        'name': item.get('name', 'Untitled'),
                        'id': item.get('id', ''),
                        'track_count': item.get('tracks', {}).get('total', 0)
                    })
        
        return playlists
    
    def get_playlist_tracks(self, playlist_id: str) -> List[Dict]:
        """Fetch tracks from a specific playlist."""
        if not self.sp:
            return []
        
        songs = []
        try:
            with console.status("[info]Fetching playlist tracks...[/info]"):
                # First try to get playlist info to see ownership
                try:
                    playlist_info = self.sp.playlist(playlist_id, fields='owner,public,collaborative,name')
                    console.print(f"[dim]Playlist: {playlist_info.get('name', 'Unknown')}[/dim]")
                    console.print(f"[dim]Owner: {playlist_info['owner']['display_name']}[/dim]")
                    console.print(f"[dim]Public: {playlist_info.get('public', 'Unknown')}[/dim]")
                    console.print(f"[dim]Collaborative: {playlist_info.get('collaborative', 'Unknown')}[/dim]")
                except Exception as e:
                    console.print(f"[warning]Could not fetch playlist info: {e}[/warning]")
                
                # Try Method 1: Get full playlist object with tracks embedded
                try:
                    console.print("[dim]Trying full playlist fetch...[/dim]")
                    full_playlist = self.sp.playlist(playlist_id, fields='tracks.items(track(name,artists,album))')
                    
                    if 'tracks' in full_playlist and 'items' in full_playlist['tracks']:
                        for item in full_playlist['tracks']['items']:
                            if item and item.get('track'):
                                track = item['track']
                                if track and track.get('name'):
                                    songs.append({
                                        'name': track['name'],
                                        'artist': track['artists'][0]['name'] if track.get('artists') else 'Unknown',
                                        'album': track['album']['name'] if track.get('album') else 'Unknown',
                                        'search_query': f"{track['artists'][0]['name']} - {track['name']}" if track.get('artists') else track['name']
                                    })
                        
                        if songs:
                            console.print(f"[success]Successfully loaded {len(songs)} tracks via full playlist![/success]")
                            return songs
                        else:
                            raise Exception("Full playlist returned empty tracks")
                            
                except Exception as e1:
                    console.print(f"[warning]Full playlist method failed: {e1}[/warning]")
                    
                    # Try Method 2: Use current_user_playlists and find this one
                    try:
                        console.print("[dim]Trying user playlists method...[/dim]")
                        user_playlists = self.sp.current_user_playlists(limit=50)
                        
                        for pl in user_playlists['items']:
                            if pl['id'] == playlist_id:
                                if 'tracks' in pl and pl['tracks'].get('total', 0) > 0:
                                    # Now fetch with correct context
                                    tracks_result = self.sp.user_playlist_tracks(
                                        user=pl['owner']['id'],
                                        playlist_id=playlist_id
                                    )
                                    
                                    for item in tracks_result.get('items', []):
                                        if item and item.get('track'):
                                            track = item['track']
                                            if track and track.get('name'):
                                                songs.append({
                                                    'name': track['name'],
                                                    'artist': track['artists'][0]['name'] if track.get('artists') else 'Unknown',
                                                    'album': track['album']['name'] if track.get('album') else 'Unknown',
                                                    'search_query': f"{track['artists'][0]['name']} - {track['name']}" if track.get('artists') else track['name']
                                                })
                                    
                                    if songs:
                                        console.print(f"[success]Successfully loaded {len(songs)} tracks via user playlists![/success]")
                                        return songs
                                break
                        
                        raise Exception("Playlist not found in user playlists")
                        
                    except Exception as e2:
                        console.print(f"[warning]User playlists method failed: {e2}[/warning]")
                        raise Exception(f"All methods failed")
                
        except Exception as e:
            console.print(f"[danger]Error accessing playlist: {e}[/danger]")
            console.print("[warning]This appears to be a Spotify API issue.[/warning]")
            return []
        
        return songs
    
    def get_top_artists(self, limit: int = 20) -> List[Dict]:
        """Fetch user's top artists."""
        if not self.sp:
            return []
        
        artists = []
        with console.status("[info]Fetching top artists...[/info]"):
            results = self.sp.current_user_top_artists(limit=limit)
            for item in results['items']:
                artists.append({
                    'name': item['name'],
                    'genres': item['genres'][:3] if item['genres'] else []
                })
        
        return artists
    
    def get_top_tracks(self, limit: int = 50) -> List[Dict]:
        """Fetch user's top tracks."""
        if not self.sp:
            return []
        
        songs = []
        with console.status("[info]Fetching top tracks...[/info]"):
            results = self.sp.current_user_top_tracks(limit=limit)
            for item in results['items']:
                songs.append({
                    'name': item['name'],
                    'artist': item['artists'][0]['name'],
                    'search_query': f"{item['artists'][0]['name']} - {item['name']}"
                })
        
        return songs
    
    def get_followed_artists(self) -> List[Dict]:
        """Fetch artists the user follows."""
        if not self.sp:
            return []
        
        artists = []
        with console.status("[info]Fetching followed artists...[/info]"):
            results = self.sp.current_user_followed_artists(limit=50)
            for item in results['artists']['items']:
                artists.append({
                    'name': item['name'],
                    'id': item['id'],
                    'genres': item.get('genres', [])[:3],
                    'popularity': item.get('popularity', 0)
                })
        
        return artists
    
    def get_artist_albums(self, artist_id: str) -> List[Dict]:
        """Fetch albums by a specific artist."""
        if not self.sp:
            return []
        
        albums = []
        with console.status("[info]Fetching artist albums...[/info]"):
            results = self.sp.artist_albums(artist_id, album_type='album', limit=50)
            for item in results['items']:
                albums.append({
                    'name': item['name'],
                    'id': item['id'],
                    'release_date': item.get('release_date', 'Unknown'),
                    'total_tracks': item.get('total_tracks', 0)
                })
        
        return albums
    
    def get_album_tracks(self, album_id: str) -> List[Dict]:
        """Fetch tracks from a specific album."""
        if not self.sp:
            return []
        
        songs = []
        with console.status("[info]Fetching album tracks...[/info]"):
            results = self.sp.album_tracks(album_id)
            for item in results['items']:
                songs.append({
                    'name': item['name'],
                    'artist': item['artists'][0]['name'],
                    'search_query': f"{item['artists'][0]['name']} - {item['name']}"
                })
        
        return songs

# ── Formatting Helpers ───────────────────────────────────────────────────────

def fmt_duration(seconds) -> str:
    h, r = divmod(int(seconds or 0), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def fmt_views(n) -> str:
    if n is None: return "N/A"
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_size(b) -> str:
    if b is None: return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def make_progress() -> Progress:
    return Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def nav_hint(options: list[str]) -> None:
    console.print(f"[nav]  ( {' · '.join(options)} )[/nav]")


def show_banner() -> None:
    banner = Text()
    banner.append("  ▶  ", style="bold red")
    banner.append("YouTube Downloader Pro", style="bold white")
    banner.append("  + Spotify", style="bold green")
    banner.append("  ▶  ", style="bold red")
    console.print(Panel(banner, style="bold blue", padding=(1, 4)))


# ── Audio Format Conversion ─────────────────────────────────────────────────

def convert_audio(input_path: Path, output_format: str, bitrate: str = "320k") -> Path:
    """Convert audio file to specified format using FFmpeg."""
    output_path = input_path.with_suffix(f".{output_format}")
    console.print(f"[info]Converting to {output_format.upper()}...[/info]")

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vn", "-ar", "44100", "-ac", "2",
    ]
    if output_format.lower() not in ["wav", "flac"]:
        cmd.extend(["-b:a", bitrate])
    cmd.append(str(output_path))

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.remove(input_path)
        console.print(f"[success]✔ Converted to {output_format.upper()}[/success]")
        return output_path
    except Exception as e:
        console.print(f"[danger]Conversion failed: {e}[/danger]")
        console.print("[warning]Keeping original file[/warning]")
        return input_path


def normalize_audio(file_path: Path) -> None:
    """Normalize audio volume using FFmpeg loudnorm filter."""
    if not check_ffmpeg():
        return
    temp_path = file_path.with_suffix(".norm" + file_path.suffix)
    cmd = [
        "ffmpeg", "-y", "-i", str(file_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        str(temp_path)
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.replace(temp_path, file_path)
        console.print("[success]✔ Audio normalized[/success]")
    except Exception as e:
        console.print(f"[warning]Normalization skipped: {e}[/warning]")
        if temp_path.exists():
            os.remove(temp_path)


def embed_tags(file_path: Path, title: str, artist: str, album: str,
               album_art_url: str = "") -> None:
    """Embed ID3 metadata tags into audio file."""
    if not MUTAGEN_AVAILABLE:
        return

    try:
        suffix = file_path.suffix.lower()

        # Fetch album art bytes if URL provided
        art_data = None
        if album_art_url:
            try:
                with urllib.request.urlopen(album_art_url, timeout=5) as r:
                    art_data = r.read()
            except Exception:
                pass

        if suffix == ".mp3":
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                tags = ID3()
            tags["TIT2"] = TIT2(encoding=3, text=title)
            tags["TPE1"] = TPE1(encoding=3, text=artist)
            tags["TALB"] = TALB(encoding=3, text=album)
            if art_data:
                tags["APIC"] = APIC(encoding=3, mime="image/jpeg",
                                    type=3, desc="Cover", data=art_data)
            tags.save(str(file_path))

        elif suffix == ".flac":
            audio = FLAC(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            if art_data:
                from mutagen.flac import Picture
                pic = Picture()
                pic.data = art_data
                pic.mime = "image/jpeg"
                pic.type = 3
                audio.add_picture(pic)
            audio.save()

        elif suffix == ".m4a":
            audio = MP4(str(file_path))
            audio["\xa9nam"] = title
            audio["\xa9ART"] = artist
            audio["\xa9alb"] = album
            if art_data:
                from mutagen.mp4 import MP4Cover
                audio["covr"] = [MP4Cover(art_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()

        elif suffix == ".ogg":
            audio = OggVorbis(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            audio.save()

        console.print("[success]✔ Tags embedded[/success]")

    except Exception as e:
        console.print(f"[warning]Tag embedding skipped: {e}[/warning]")


import subprocess  # CRITICAL: Was missing in your snippet
import yt_dlp
from pathlib import Path
from typing import Optional, List, Dict

# ... (Keep your imports, Theme, and LibraryDB as they were)

# ── THE CORE ENGINE (FIXED) ──────────────────────────────────────────────────

def yt_dlp_progress_hook(d, progress_bar, task_id):
    """A robust, standalone hook to update the Rich progress bar."""
    if d['status'] == 'downloading':
        try:
            # Extract percentage from yt-dlp's string format (e.g., " 45.2%")
            p_str = d.get('_percent_str', '0%').replace('%', '').strip()
            percent = float(p_str)
            progress_bar.update(task_id, completed=percent)
        except (ValueError, TypeError):
            pass
    elif d['status'] == 'finished':
        progress_bar.update(task_id, completed=100)

def download_with_ytdlp(
    query_or_url: str, 
    out_dir: Path, 
    format_choice: str, 
    song_meta: dict = None, 
    db: LibraryDB = None,
    normalize: bool = False
) -> Optional[Path]:
    """
    Unified engine. 
    Accepts a direct URL OR a search query (using ytsearch1:).
    """
    fmt_map = {
        "1": {"ext": "mp3", "abr": "320k"},
        "2": {"ext": "mp3", "abr": "192k"},
        "3": {"ext": "wav", "abr": None},
        "4": {"ext": "flac", "abr": None},
        "5": {"ext": "ogg", "abr": "256k"},
        "6": {"ext": "m4a", "abr": "256k"}
    }
    choice = fmt_map.get(format_choice, {"ext": "m4a", "abr": "128k"})

    # If the user provided a search query instead of a URL, prefix it for yt-dlp
    # This is the 'Magic' that connects Spotify to YouTube
    search_url = query_or_url
    if not query_or_url.startswith(("http://", "https://")):
        search_url = f"ytsearch1:{query_or_url}"

    is_audio_only = format_choice in ["1", "updated", "2", "3", "4", "5", "6"]
    
    ydl_opts = {
        'cookiefile' : cookies.txt
        'outtmpl': str(out_dir / '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    if is_audio_only:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': choice['ext'],
            'preferredquality': choice['abr'] if choice['abr'] else '192',
        }]
    else:
        ydl_opts['format'] = 'bestvideo+bestaudio/best'

    # Setup Progress Bar locally to ensure thread safety and scope access
    from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn
    
    with Progress(
        TextColumn("[bold cyan]{task.description}"),

        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        
        task_id = progress.add_task(f"Downloading: {query_or_url[:30]}...", total=100)
        
        # Inject the hook
        ydl_opts['progress_hooks'] = [lambda d: yt_dlp_progress_hook(d, progress, task_id)]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_url, download=True)
                # Handle case where ytsearch returns a list of entries
                if 'entries' in info:
                    info = info['entries'][0]
                
                downloaded_file = Path(ydl.prepare_filename(info))
                
                # Fix extension mismatch after FFmpeg conversion
                # (yt-dlp changes filename when converting to mp3)
                expected_path = downloaded_file.with_suffix(f".{choice['ext']}")
                if expected_path.exists():
                    download_file = expected_path
                else:
                    downloaded_file = downloaded_file

                # Database Update
                if db:
                    db.add(
                        info.get('title', 'Unknown'), 
                        info.get('uploader', 'Unknown'), 
                        "", search_url, str(downloaded_file), choice['ext'], 0, "youtube"
                    )

                return downloaded_file

        except Exception as e:
            console.print(f"[danger]Download failed: {e}[/danger]")
            return None

# ── THE AUTOMATION BRIDGE (NEW) ──────────────────────────────────────────────

def auto_download_from_spotify(
    songs: List[Dict], 
    base_path: str, 
    org_choice: str, 
    format_choice: str, 
    db=None, 
    failed_log=None
):
    """
    The 'Brain' function. Converts Spotify metadata into YouTube search queries
    and executes the download loop automatically.
    """
    v_dir, a_dir = get_subdirs(base_path)
    
    # Determine destination based on organization choice
    # (Simplified for this example: using Artist/Album logic from your bulk function)
    total = len(songs)
    console.print(f"[success]🚀 Starting Automated Spotify Sync: {total} tracks found.[/success]")

    for i, song in enumerate(songs, 1):
        # Create search string: "Artist - Track Name"
        query = f"{song['artist']} - {song['name']}"
        
        # Define folder structure (Simplified version of your bulk logic)
        # You can expand this to use your 'org_choice' logic here
        output_folder = a_dir / song['artist'].replace(" ", "_")
        output_folder.mkdir(parents=True, exist_ok=True)

        console.print(f"[dim][{i}/{total}] Searching for: {query}[/dim]")

        try:
            result = download_with_ytdlp(
                query_or_url=query, 
                out_dir=output_folder, 
                format_choice=format_choice,
                song_meta=song, 
                db=db
            )
            if not result:
                if failed_log: failed_log.add(query, "yt-dlp search failure", "spotify_bridge")
        except Exception as e:
            console.print(f"[danger]Error processing {query}: {e}[/danger]")
            if failed_log: failed_log.add(query, str(e), "spotify_bridge")

    console.print("[bold green]✨ Spotify Sync Complete![/bold green]")

# ── Stats Screen (Unchanged) ─────────────────────────────────────────────────

def show_stats_screen(db: LibraryDB) -> None:
    """Display download library stats."""
    stats = db.get_stats()
    console.print(Rule("[info]📊 Download Library Stats[/info]"))

    size_str = fmt_size(stats["total_run_size"]) if stats["total_size"] else "0 B"
    console.print(Panel(
        f"[highlight]Total Downloaded:[/highlight] {stats['total']} tracks\n"
        f"[highlight]Total Size:[/highlight] {size_str}",
        border_style="cyan"
    ))

    if stats["top_artists"]:
        t = Table(title="Top Artists", box=box.SIMPLE)
        t.add_column("Artist", style="cyan")
        t.add_column("Tracks", style="yellow")
        for artist, count in stats["top_artists"]:
            t.add_row(artist or "Unknown", str(count))
        console.print(t)

    if stats["by_format"]:
        t = Table(title="By Format", box=box.SIMPLE)
        t.add_column("Format", style="green")
        t.add_column("Tracks", style="yellow")
        for fmt, count in stats["by_format"]:
            t.add_row((fmt or "?").upper(), str(count))
        console.print(t)

    if stats["recent"]:
        t = Table(title="Recently Downloaded", box=box.SIMPLE)
        t.add_column("Title", style="white")
        t.add_column("Artist", style="cyan")
        t.add_column("When", style="dim")
        for title, artist, when in stats["api_recent"]: # Assuming updated key names from your DB
            try:
                dt = datetime.fromisoformat(when)
                when_str = dt.strftime("%d %b %Y %H:%M")
            except Exception:
                when_str = when[:16]
            t.add_row(title[:35], artist or "?", when_str)
        console.print(t)

    Prompt.ask("\n[muted]Press Enter to go back[/muted]", default="")


# ── Bulk Download Engine (Unchanged/Refined) ─────────────────────────────────

def bulk_download_songs(songs: List[Dict], base_path: str, org_choice: str,
                        format_choice: str, folder_name: str,
                        db: LibraryDB = None, failed_log: FailedLog = None,
                        normalize: bool = False, yes_all: bool = False,
                        min_confidence: float = 40.0) -> None:
    v_dir, a_dir = get_subdirs(base_path)
    total = len(songs)
    successful = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    console.print(f"\n[info]Starting bulk download of {total} tracks...[/info]")

    for i, song in enumerate(songs, 1):
        elapsed = time.time() - start_time
        eta_str = str(timedelta(seconds=int(elapsed * (total - i + 1) / i))) if i > 1 else "calculating..."

        console.print(Rule(f"[dim]{i}/{total}  ETA: {eta_str}  ─  {song.get('name', 'Unknown')[:50]}[/dim]"))

        def safe(s): return "".join(c for c in s if c.isalnum() or c in " -_").strip()
        if org_choice == "1": output_folder = a_dir / safe(folder_name)
        elif org_choice == "2": output_folder = a_dir / safe(song.get("artist", "Unknown"))
        elif org_choice == "3": output_folder = a_dir / safe(song.get("album", "Unknown"))
        else: output_folder = a_dir / safe(song.get("artist", "Unknown")) / safe(song.get("album", folder_name))

        output_folder.mkdir(parents=True, exist_ok=True)

        # Use the new yt-dlp download function
        try:
            # Note: We use the URL directly as it's more reliable with yt-dlp
            url = song.get("youtube_url") or song.get("search_query")
            if not url:
                raise ValueError("No URL found for track")

            result = download_with_ytdlp( # Calling the NEW engine function
                url, out_dir=output_folder, format_choice=format_choice,
                song_meta=song, db=db, normalize=normalize
            )
            if result: successful += 1
            else: skipped += 1

        except Exception as e:
            console.print(f"  [danger]✘ Download failed: {e}[/danger]")
            if failed_log: failed_log.add(song.get("name", "Unknown"), str(e), "bulk")
            failed += 1

    total_time = str(timedelta(seconds=int(time.time() - start_time)))
    console.print(Panel(f"[success]✔ Downloaded: {successful}[/success]\n[warning]⟳ Skipped: {skipped}[/warning]\n[danger]✘ Failed: {failed}[/danger]", 
                        title="Bulk Download Complete", border_style="green"))
    notify("Download Complete!", f"{successful} downloaded, {failed} failed.")

def get_bulk_options() -> Tuple[str, str, bool, bool, float]:
    """Ask user for format, normalization, yes-all, and min confidence."""
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return None, None, False, False, 40.0

    normalize = Confirm.ask("[info]Normalize audio volume?[/info]", default=False)
    yes_all = Confirm.ask("[info]Skip all confirmation prompts? (yes to all)[/info]", default=False)

    console.print("[info]Minimum confidence score to download (0=download everything, 70=strict matching)[/info]")
    min_conf_str = Prompt.ask("[info]Min confidence %[/info]", default="40")
    try:
        min_conf = float(min_conf_str)
    except Exception:
        min_conf = 40.0

    return format_choice, normalize, yes_all, min_conf



# ── Spotify Integration Flows (Updated to use yt-dlp) ────────────────────────

def retry_failed_downloads(failed_log: FailedLog, base_path: str, db: LibraryDB) -> None:
    """Retry all previously failed downloads."""
    entries = failed_log.entries
    if not entries:
        console.print("[info]No failed downloads to retry![/info]")
        return

    console.print(f"[info]Found {len(entries)} failed downloads[/info]\n")
    table = Table(title="Failed Downloads", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Query", style="white")
    table.add_column("Reason", style="dim")
    for i, e in enumerate(entries[:20], 1):
        table.add_row(str(i), e['query'][:50], e.get('reason','')[:30])
    console.print(table)

    if not Confirm.ask("[info]Retry all?[/info]", default=True):
        return

    format_choice, normalize, yes_all, min_conf = get_bulk_options()
    if not format_choice:
        return

    songs = [{"name": e["query"], "search_query": e["query"],
               "artist": "", "album": ""} for e in entries]

    failed_log.clear()

    v_dir, a_dir = get_subdirs(base_path)
    retry_folder = a_dir / "Retried Downloads"
    retry_folder.mkdir(parents=True, exist_ok=True)

    bulk_download_songs(songs, base_path, "1", format_choice, "Retried Downloads",
                        db=db, failed_log=failed_log,
                        normalize=normalize, yes_all=yes_all, min_confidence=min_conf)


def spotify_import_flow(spotify_mgr: SpotifyManager, base_path: str, db=None, failed_log=None) -> str:
    if not spotify_mgr.sp:
        if not spotify_mgr.setup_spotify(): return "back"
    
    while True:
        console.print(Rule(style="green"))
        console.print("[info]Spotify Import Options:[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Import Liked Songs\n  [bold yellow]2[/bold yellow]  Import from Playlist\n  [bold yellow]3[/bold yellow]  Import Top Tracks\n  [bold yellow]b[/bold yellow]  Back")
        choice = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()
        if choice == "b": return "back"
        elif choice == "1": import_liked_songs(spotify_mgr, base_path, db, failed_log)
        elif choice == "2": import_playlist(spotify_mgr, base_path, db, failed_log)
        elif choice == "3": import_top_tracks(spotify_mgr, base_path, db, failed_log)

def import_liked_songs(spotify_mgr, base_path, db=None, failed_log=None):
    limit = 999999 if Confirm.ask("[info]Download ALL liked songs?[/info]", default=True) else 50
    songs = spotify_mgr.get_liked_songs(limit=limit)
    
    if not songs: return

    console.print(f"[success]Found {len(songs)} liked songs![/success]\n")

    table = Table(title="Preview (First 10)", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Artist", style="cyan")
    table.add_column("Song", style="white")
    table.add_column("Album", style="dim")
    for i, song in enumerate(songs[:10], 1):
       table.add_row(str(i), song['artist'], song['name'], song.get('album','')[:30])
    console.print(table)
    if Confirm.ask(f"\n[info]Begin automated download of {len(songs)} songs?[/info]", default=True):
            format_choice = get_bulk_format_choice() # Ensure this helper exists!
            
            # This is the new way:
            auto_download_from_spotify(
                songs=songs, 
                base_path=base_path, 
                org_choice="2", # Example: By Artist
                format_choice=format_choice,
                db=db,
                failed_log=failed_log
            )
    if not Confirm.ask(f"\n[info]Download all {len(songs)} songs?[/info]", default=True):
        return
    console.print("\n[info]How should files be organized?[/info]")
    console.print("  [bold yellow]1[/bold yellow]  All in one folder (Liked Songs)")
    console.print("  [bold yellow]2[/bold yellow]  By Artist")
    console.print("  [bold yellow]3[/bold yellow]  By Album")
    console.print("  [bold yellow]4[/bold yellow]  By Artist/Album")
    org_choice = Prompt.ask("[info]Organization[/info]", choices=["1","2","3","4"], default="1")
    format_choice = get_bulk_format_choice()
    if format_choice:
        bulk_download_songs(songs, base_path, org_choice, format_choice, "Liked Songs", db=db, failed_log=failed_log)

def download_playlist_from_youtube(playlist_name: str, base_path: str) -> None:
    """Download playlist by searching YouTube with the playlist name."""
    
    console.print(f"\n[info]Searching YouTube for: '{playlist_name}'[/info]")
    
    # Ask how many results to download
    num_songs = int(Prompt.ask("[info]How many songs to download from search results?[/info]", default="20"))
    
    # Search YouTube
    search_query = f"{playlist_name} playlist"
    
    with console.status(f"[info]Searching YouTube...[/info]"):
        search = Search(search_query)
    
    if not search.videos:
        console.print("[warning]No results found on YouTube[/warning]")
        return
    
    # Show preview
    console.print(f"\n[success]Found results on YouTube[/success]")
    table = Table(title=f"Preview (First 10)", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Title", style="white")
    table.add_column("Duration", style="dim")
    
    for i, video in enumerate(search.videos[:10], 1):
        table.add_row(str(i), video.title[:60], fmt_duration(video.length))
    
    console.print(table)
    
    if not Confirm.ask(f"\n[info]Download top {num_songs} results?[/info]", default=True):
        return
    
    # Get format
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    # Create folder
    v_dir, a_dir = get_subdirs(base_path)
    safe_name = "".join([c for c in playlist_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    playlist_folder = a_dir / safe_name
    playlist_folder.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[info]Saving to: {playlist_folder}[/info]\n")
    
    # Download
    successful = 0
    for i, video in enumerate(search.videos[:num_songs], 1):
        console.print(Rule(f"[dim]{i}/{num_songs}: {video.title[:50]}[/dim]"))
        try:
            yt = YouTube(video.watch_url, use_oauth=True, client='WEB_CREATOR')
            download_audio_format(yt, playlist_folder, format_choice)
            successful += 1
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
    
    console.print(f"\n[success]✔ Downloaded {successful}/{num_songs} tracks to '{safe_name}' folder[/success]")



    format_choice = get_bulk_format_choice()
    if format_choice:
        bulk_download_songs(songs, base_path, "1", format_choice, selected_playlist['name'], db=db, failed_log=failed_log)
def import_playlist(spotify_mgr: SpotifyManager, base_path: str,
                     db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """Import songs from a Spotify playlist."""
    
    playlists = spotify_mgr.get_playlists()
    if not playlists:
        console.print("[warning]No playlists found![/warning]")
        return
    
    # Filter to only playlists we can access (owned by user or public)
    # We'll show all but handle errors gracefully when accessing
    
    # Show playlists
    table = Table(title="Your Playlists", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Name", style="white")
    table.add_column("Tracks", style="dim")
    
    for i, pl in enumerate(playlists, 1):
        table.add_row(str(i), pl['name'], str(pl['track_count']))
    
    console.print(table)
    console.print("\n[muted]Note: Some playlists may be private/restricted and won't be accessible.[/muted]")
    
    choice = Prompt.ask("[info]Select playlist #[/info]", default="b")
    if choice.lower() == "b":
        return
    
    if not choice.isdigit() or not (1 <= int(choice) <= len(playlists)):
        console.print("[warning]Invalid selection[/warning]")
        return
    
    selected_playlist = playlists[int(choice) - 1]
    songs = spotify_mgr.get_playlist_tracks(selected_playlist['id'])
    
    # If that fails or returns empty, offer YouTube search workaround
    if not songs or len(songs) == 0:
        console.print(f"\n[warning]Could not fetch tracks from Spotify API[/warning]")
        console.print("[info]This is a known Spotify API bug with some playlists.[/info]\n")
        
        # Offer workaround
        console.print("[highlight]Workaround Options:[/highlight]")
        console.print(f"  [bold yellow]1[/bold yellow]  Search YouTube for '{selected_playlist['name']}' and download results")
        console.print("  [bold yellow]2[/bold yellow]  Try a different playlist")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        
        workaround_choice = Prompt.ask("[info]Choice[/info]", choices=["1", "2", "b"], default="b")
        
        if workaround_choice == "b":
            return
        elif workaround_choice == "2":
            return import_playlist(spotify_mgr, base_path)  # Try again
        elif workaround_choice == "1":
            # YouTube search workaround
            download_playlist_from_youtube(selected_playlist['name'], base_path)
            return
        console.print("[info]Try selecting a different playlist or one that you created yourself.[/info]")
        return
    
    console.print(f"[success]Found {len(songs)} tracks in '{selected_playlist['name']}'[/success]\n")
    
    if not Confirm.ask("[info]Download all tracks?[/info]", default=True):
        return
    
    # Ask how to organize
    console.print("\n[info]How should files be organized?[/info]")
    console.print(f"  [bold yellow]1[/bold yellow]  All in one folder ({selected_playlist['name']})")
    console.print("  [bold yellow]2[/bold yellow]  By Artist")
    console.print("  [bold yellow]3[/bold yellow]  By Album")
    console.print("  [bold yellow]4[/bold yellow]  By Artist/Album")
    
    org_choice = Prompt.ask("[info]Organization[/info]", choices=["1", "2", "3", "4"], default="1")
    
    # Get format and download
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    # Set up base directory
    v_dir, a_dir = get_subdirs(base_path)
    safe_playlist = "".join([c for c in selected_playlist['name'] if c.isalnum() or c in (' ', '-', '_')]).strip()
    
    successful = 0
    
    for i, song in enumerate(songs, 1):
        console.print(Rule(f"[dim]{i}/{len(songs)}: {song['search_query']}[/dim]"))
        try:
            # Determine output folder based on organization choice
            if org_choice == "1":
                output_folder = a_dir / safe_playlist
            elif org_choice == "2":
                safe_artist = "".join([c for c in song['artist'] if c.isalnum() or c in (' ', '-', '_')]).strip()
                output_folder = a_dir / safe_artist
            elif org_choice == "3":
                # For playlists we don't have album info, use artist as fallback
                safe_artist = "".join([c for c in song['artist'] if c.isalnum() or c in (' ', '-', '_')]).strip()
                output_folder = a_dir / safe_artist
            else:  # org_choice == "4"
                safe_artist = "".join([c for c in song['artist'] if c.isalnum() or c in (' ', '-', '_')]).strip()
                output_folder = a_dir / safe_artist / safe_playlist
            
            output_folder.mkdir(parents=True, exist_ok=True)
            
            search = Search(song['search_query'])
            if search.videos:
                yt = YouTube(search.videos[0].watch_url, use_oauth=True, client='WEB_CREATOR')
                download_audio_format(yt, output_folder, format_choice)
                successful += 1
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
    
    console.print(f"[success]✔ Downloaded {successful}/{len(songs)} tracks[/success]")


def import_top_tracks(spotify_mgr: SpotifyManager, base_path: str,
                       db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """Import user's top tracks from Spotify."""
    
    # Ask if they want ALL or a limit
    if Confirm.ask("[info]Download ALL top tracks?[/info]", default=True):
        limit = 50  # Spotify API limit for top tracks
    else:
        limit = int(Prompt.ask("[info]How many top tracks to import?[/info]", default="20"))
    
    songs = spotify_mgr.get_top_tracks(limit=limit)
    
    if not songs:
        console.print("[warning]No top tracks found![/warning]")
        return
    
    console.print(f"[success]Found {len(songs)} top tracks![/success]")
    
    if not Confirm.ask("[info]Download all?[/info]", default=True):
        return
    
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    # Create "Top Tracks" folder
    v_dir, a_dir = get_subdirs(base_path)
    top_folder = a_dir / "Top Tracks"
    top_folder.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[info]Saving to: {top_folder}[/info]\n")
    
    successful = 0
    
    for i, song in enumerate(songs, 1):
        console.print(Rule(f"[dim]{i}/{len(songs)}: {song['search_query']}[/dim]"))
        try:
            search = Search(song['search_query'])
            if search.videos:
                yt = YouTube(search.videos[0].watch_url, use_oauth=True, client='WEB_CREATOR')
                download_audio_format(yt, top_folder, format_choice)
                successful += 1
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
    
    console.print(f"[success]✔ Downloaded {successful}/{len(songs)} tracks to 'Top Tracks' folder[/success]")


def download_artist_top_tracks(spotify_mgr: SpotifyManager, base_path: str,
                                db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """Download top tracks from a specific artist."""
    
    # Let user search for artist or pick from top artists
    choice = Prompt.ask(
        "[info]Search for artist (s) or pick from your top artists (t)?[/info]",
        choices=["s", "t"],
        default="t"
    )
    
    if choice == "t":
        artists = spotify_mgr.get_top_artists(limit=20)
        if not artists:
            console.print("[warning]No artists found![/warning]")
            return
        
        table = Table(title="Your Top Artists", box=box.SIMPLE)
        table.add_column("#", style="yellow")
        table.add_column("Artist", style="white")
        table.add_column("Genres", style="dim")
        
        for i, artist in enumerate(artists, 1):
            genres = ", ".join(artist['genres']) if artist['genres'] else "N/A"
            table.add_row(str(i), artist['name'], genres)
        
        console.print(table)
        
        sel = Prompt.ask("[info]Select artist #[/info]", default="b")
        if sel.lower() == "b":
            return
        
        if not sel.isdigit() or not (1 <= int(sel) <= len(artists)):
            console.print("[warning]Invalid selection[/warning]")
            return
        
        artist_name = artists[int(sel) - 1]['name']
    else:
        artist_name = Prompt.ask("[info]Enter artist name[/info]")
    
    # Search for top tracks by this artist
    console.print(f"[info]Searching for {artist_name} top tracks...[/info]")
    
    # Get format choice
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    # Create artist folder
    v_dir, a_dir = get_subdirs(base_path)
    safe_name = "".join([c for c in artist_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    artist_folder = a_dir / safe_name
    artist_folder.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[info]Saving to: {artist_folder}[/info]\n")
    
    # Search YouTube for artist's popular songs
    queries = [
        f"{artist_name} greatest hits",
        f"{artist_name} top songs",
        f"{artist_name} best of",
        f"{artist_name} popular"
    ]
    
    successful = 0
    seen_titles = set()  # Avoid duplicates
    
    for query in queries:
        console.print(Rule(f"[dim]Searching: {query}[/dim]"))
        try:
            search = Search(query)
            for video in search.videos[:5]:  # Top 5 from each search
                if video.title.lower() not in seen_titles:
                    seen_titles.add(video.title.lower())
                    try:
                        yt = YouTube(video.watch_url, use_oauth=True, client='WEB_CREATOR')
                        download_audio_format(yt, artist_folder, format_choice)
                        successful += 1
                    except Exception as e:
                        console.print(f"[danger]Skipped {video.title}: {e}[/danger]")
        except Exception as e:
            console.print(f"[danger]Search failed: {e}[/danger]")
    
    console.print(f"[success]✔ Downloaded {successful} tracks to '{safe_name}' folder[/success]")


def download_artist_albums(spotify_mgr: SpotifyManager, base_path: str,
                            db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """Download albums from a specific artist."""
    
    artist_name = Prompt.ask("[info]Enter artist name[/info]")
    
    console.print(f"[info]Searching for {artist_name} albums...[/info]")
    
    # Get format choice
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    # Create artist folder
    v_dir, a_dir = get_subdirs(base_path)
    safe_name = "".join([c for c in artist_name if c.isalnum() or c in (' ', '-', '_')]).strip()
    artist_folder = a_dir / safe_name
    artist_folder.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[info]Saving to: {artist_folder}[/info]\n")
    
    # Search for full albums
    queries = [
        f"{artist_name} full album",
        f"{artist_name} discography",
        f"{artist_name} complete album"
    ]
    
    successful = 0
    
    for query in queries:
        console.print(Rule(f"[dim]Searching: {query}[/dim]"))
        try:
            search = Search(query)
            for video in search.videos[:3]:  # Top 3 from each search
                try:
                    yt = YouTube(video.watch_url, use_oauth=True, client='WEB_CREATOR')
                    download_audio_format(yt, artist_folder, format_choice)
                    successful += 1
                except Exception as e:
                    console.print(f"[danger]Skipped: {e}[/danger]")
        except Exception as e:
            console.print(f"[danger]Search failed: {e}[/danger]")
    
    console.print(f"[success]✔ Downloaded {successful} items to '{safe_name}' folder[/success]")


def import_followed_artists(spotify_mgr: SpotifyManager, base_path: str,
                             db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """Import music from artists the user follows on Spotify."""
    
    artists = spotify_mgr.get_followed_artists()
    
    if not artists:
        console.print("[warning]No followed artists found![/warning]")
        console.print("[info]Follow some artists on Spotify first![/info]")
        return
    
    console.print(f"[success]Found {len(artists)} followed artists![/success]\n")
    
    # Show artists table
    table = Table(title="Your Followed Artists", box=box.SIMPLE)
    table.add_column("#", style="yellow", width=4)
    table.add_column("Artist", style="white")
    table.add_column("Genres", style="dim")
    table.add_column("Popularity", style="cyan")
    
    for i, artist in enumerate(artists, 1):
        genres = ", ".join(artist['genres']) if artist['genres'] else "N/A"
        table.add_row(str(i), artist['name'], genres[:30], str(artist['popularity']))
    
    console.print(table)
    
    # Ask what to download
    console.print("\n[info]What would you like to download?[/info]")
    console.print("  [bold yellow]1[/bold yellow]  Download ALL artists (top songs each)")
    console.print("  [bold yellow]2[/bold yellow]  Download ALL artists (full albums)")
    console.print("  [bold yellow]3[/bold yellow]  Select specific artist")
    console.print("  [bold yellow]b[/bold yellow]  Back")
    
    choice = Prompt.ask("[info]Choice[/info]", choices=["1", "2", "3", "b"], default="b")
    
    if choice == "b":
        return
    elif choice == "1":
        # Download top songs for all artists
        download_all_artists_top_songs(spotify_mgr, artists, base_path, db=db, failed_log=failed_log)
    elif choice == "2":
        # Download albums for all artists
        download_all_artists_albums(spotify_mgr, artists, base_path, db=db, failed_log=failed_log)
    elif choice == "3":
        # Select specific artist
        download_specific_followed_artist(spotify_mgr, artists, base_path, db=db, failed_log=failed_log)


def download_all_artists_top_songs(spotify_mgr: SpotifyManager, artists: List[Dict],
                                    base_path: str, db: LibraryDB = None,
                                    failed_log: FailedLog = None) -> None:
    """Download top songs for all followed artists."""
    console.print(f"\n[info]Downloading top songs from {len(artists)} artists...[/info]")

    format_choice, normalize, yes_all, min_conf = get_bulk_options()
    if not format_choice:
        return

    v_dir, a_dir = get_subdirs(base_path)

    for i, artist in enumerate(artists, 1):
        console.print(Rule(f"[bold cyan]{i}/{len(artists)}: {artist['name']}[/bold cyan]"))
        safe_name = "".join(c for c in artist['name'] if c.isalnum() or c in " -_").strip()
        artist_folder = a_dir / safe_name
        artist_folder.mkdir(parents=True, exist_ok=True)

        # Build song list from YouTube search
        songs = []
        for query in [f"{artist['name']} greatest hits", f"{artist['name']} top songs"]:
            try:
                search = Search(query)
                for video in search.videos[:5]:
                    songs.append({
                        "name": video.title,
                        "artist": artist['name'],
                        "album": "",
                        "search_query": video.title,
                        "youtube_url": video.watch_url
                    })
                break
            except Exception:
                continue

        if songs:
            bulk_download_songs(songs, base_path, "2", format_choice, artist['name'],
                                db=db, failed_log=failed_log,
                                normalize=normalize, yes_all=yes_all, min_confidence=min_conf)

    console.print(f"\n[success]✔ Completed all {len(artists)} artists![/success]")
    notify("Artists Downloaded!", f"Finished downloading top songs from {len(artists)} artists")


def download_all_artists_albums(spotify_mgr: SpotifyManager, artists: List[Dict],
                                 base_path: str, db: LibraryDB = None,
                                 failed_log: FailedLog = None) -> None:
    """Download albums for all followed artists."""
    console.print(f"\n[info]Downloading albums from {len(artists)} artists...[/info]")

    format_choice, normalize, yes_all, min_conf = get_bulk_options()
    if not format_choice:
        return

    v_dir, a_dir = get_subdirs(base_path)

    for i, artist in enumerate(artists, 1):
        console.print(Rule(f"[bold cyan]{i}/{len(artists)}: {artist['name']}[/bold cyan]"))
        try:
            albums = spotify_mgr.get_artist_albums(artist.get('id', ''))
            if not albums:
                console.print(f"  [warning]No albums found for {artist['name']}[/warning]")
                continue

            console.print(f"  [info]Found {len(albums)} albums[/info]")

            for album in albums[:3]:
                safe_artist = "".join(c for c in artist['name'] if c.isalnum() or c in " -_").strip()
                safe_album = "".join(c for c in album['name'] if c.isalnum() or c in " -_").strip()

                console.print(f"  [dim]Album: {album['name']}[/dim]")
                tracks = spotify_mgr.get_album_tracks(album['id'])
                for t in tracks:
                    t['artist'] = artist['name']
                    t['album'] = album['name']

                if tracks:
                    bulk_download_songs(tracks, base_path, "4", format_choice, album['name'],
                                        db=db, failed_log=failed_log,
                                        normalize=normalize, yes_all=yes_all,
                                        min_confidence=min_conf)

        except Exception as e:
            console.print(f"[danger]Failed for {artist['name']}: {e}[/danger]")

    console.print(f"\n[success]✔ Completed all {len(artists)} artists![/success]")
    notify("Albums Downloaded!", f"Finished downloading albums from {len(artists)} artists")


def download_specific_followed_artist(spotify_mgr: SpotifyManager, artists: List[Dict],
                                       base_path: str, db: LibraryDB = None,
                                       failed_log: FailedLog = None) -> None:
    """Download from a specific followed artist."""
    
    sel = Prompt.ask("[info]Select artist #[/info]", default="b")
    if sel.lower() == "b":
        return
    
    if not sel.isdigit() or not (1 <= int(sel) <= len(artists)):
        console.print("[warning]Invalid selection[/warning]")
        return
    
    artist = artists[int(sel) - 1]
    
    console.print(f"\n[info]What to download from {artist['name']}?[/info]")
    console.print("  [bold yellow]1[/bold yellow]  Top Songs")
    console.print("  [bold yellow]2[/bold yellow]  All Albums")
    console.print("  [bold yellow]b[/bold yellow]  Back")
    
    choice = Prompt.ask("[info]Choice[/info]", choices=["1", "2", "b"], default="b")
    
    if choice == "b":
        return
    elif choice == "1":
        download_all_artists_top_songs(spotify_mgr, [artist], base_path, db=db, failed_log=failed_log)
    elif choice == "2":
        download_all_artists_albums(spotify_mgr, [artist], base_path, db=db, failed_log=failed_log)


def get_bulk_format_choice() -> str:
    """Get format choice for bulk downloads."""
    console.print("\n[info]Choose download format for all tracks:[/info]")
    console.print("  [bold yellow]1[/bold yellow]  MP3 320kbps")
    console.print("  [bold yellow]2[/bold yellow]  MP3 192kbps")
    console.print("  [bold yellow]3[/bold yellow]  WAV")
    console.print("  [bold yellow]4[/bold yellow]  FLAC")
    console.print("  [bold yellow]5[/bold yellow]  OGG")
    console.print("  [bold yellow]6[/bold yellow]  M4A")
    
    has_ffmpeg = check_ffmpeg()
    if not has_ffmpeg:
        console.print("[warning]⚠ FFmpeg required for formats 1-5[/warning]")
    
    choice = Prompt.ask("[info]Format[/info]", default="1")
    return choice if choice in ["1", "2", "3", "4", "5", "6"] else None


def search_flow_with_query(query: str, base_path: str) -> str:
    """Search with a pre-filled query."""
    with console.status(f"[info]Searching for: {query}[/info]"):
        s = Search(query)

    if not s.videos:
        console.print("[warning]No results found.[/warning]")
        return "back"

    # Results Table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
    table.add_column("#", style="yellow", width=4)
    table.add_column("Title", style="white")
    table.add_column("Length", style="dim")

    top_vids = s.videos[:10]
    for i, v in enumerate(top_vids, 1):
        table.add_row(str(i), v.title, fmt_duration(v.length))

    console.print(table)

    c = Prompt.ask("[info]Pick #[/info]", default="b")
    if c.lower() == "b":
        return "back"
    if c.isdigit() and 1 <= int(c) <= len(top_vids):
        url = top_vids[int(c) - 1].watch_url
        return load_and_route(url, base_path)
    
    return "back"


# ── YouTube Search & URL Flows (Updated to yt-dlp) ──────────────────────────
def show_metadata(yt: YouTube) -> None:
    info = (
        f"[bold white]Title:[/]      [highlight]{yt.title}[/]\n"
        f"[bold white]Author:[/]     [info]{yt.author}[/]\n"
        f"[bold white]Duration:[/]   {fmt_duration(yt.length)}\n"
        f"[bold white]Views:[/]      {fmt_views(yt.views)}\n"
        f"[bold white]Date:[/]       {yt.publish_date.date() if yt.publish_date else 'N/A'}"
    )
    console.print(Panel(info, title="[title] Video Info [/title]", border_style="blue"))

def search_flow(base_path: str) -> str:
    """Search using yt-dlp extractor."""
    query = Prompt.ask("\n[info]Search YouTube[/info]", default="b").strip()
    if query.lower() in ["b", "q"]: return query

    with console.status(f"[info]Searching for: {query}...[/info]"):
        try:
            # Use yt-dlp to search without downloading
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                result = ydl.extract_info(f"ytsearch10:{query}", download=False)
                entries = result.get('entries', [])
        except Exception as e:
            console.print(f"[danger]Search error: {e}[/danger]")
            return "back"

    if not entries:
        console.print("[warning]No results found.[/warning]")
        return "back"

    table = Table(title="Search Results", box=box.SIMPLE, header_style="bold cyan")
    table.add_column("#", width=4)
    table.add_column("Title")
    table.add_column("Duration", dim=True)

    for i, entry in enumerate(entries, 1):
        duration = str(timedelta(seconds=entry.get('duration', 0)))
        table.add_row(str(i), entry.get('title', 'Unknown'), duration)
    
    console.print(table)
    choice = Prompt.ask("[info]Pick #[/info]", default="b")
    if choice.isdigit() and 1 <= int(choice) <= len(entries):
        return load_and_route(entries[int(choice)-1]['webpage_url'], base_path)
    return "back"

def load_and_route(url: str, base_path: str) -> str:
    """Route URL to playlist or video menu using yt-dlp info."""
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        # Check if it's a playlist
        if 'entries' in info and info['entries']:
            return playlist_flow(url, base_path)
        else:
            return video_menu(info, base_path) # Pass the info dict, not a YouTube object
    except Exception as e:
        console.print(f"[danger]Error loading URL: {e}[/danger]")
        return "back"

def playlist_flow(url: str, base_path: str) -> str:
    """Uses yt-dlp to iterate through playlist entries."""
    with console.status("[info]Loading playlist...[/info]"):
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
    
    console.print(Panel(f"[highlight]{info.get('title', 'Playlist')}[/highlight]\n{len(info.get('entries', []))} videos"))
    
    mode = Prompt.ask("[info]Choice[/info]", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="2")
 # In a real implementation, you'd loop through info['entries'] here 
    # and call the download function for each.
    return "back"

def video_menu(info: dict, base_path: str) -> str:
    """The menu for a single video (now takes yt-dlp info dict)."""
    v_dir, a_dir = get_subdirs(base_path)
    console.print(Rule(style="cyan"))
    
    # Display metadata from the info dict
    metadata = (
        f"[bold white]Title:[/]      [highlight]{info.get('title')}[/]\n"
        f"[bold white]Uploader:[/]  [info]{info.get('uploader')}[/]\n"
        f"[bold white]Duration:[/]  {str(timedelta(seconds=info.get('duration', 0)))}[/]"
    )

    console.print(Panel(metadata, title="Video Info", border_style="blue"))
    
    console.print("[info]Actions:[/info]")
    console.print("  [bold yellow]1[/bold yellow] Best Quality Video (Merge)")
    console.print("  [bold yellow]2[/bold yellow]  Standard Video")
    console.print("  [bold yellow]3[/bold yellow]  Audio Only (.m4a)")
    console.print("  [bold yellow]5[/bold yellow]  Audio Formats (MP3/WAV)")
    console.print("  [bold yellow]b[/bold yellow]  Back")

    choice = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()
    if choice == "b": return "back"
    
    # Trigger the new download engine
    url = info['webpage_url']
    if choice == "1": download_with_ytdlp(url, v_dir, "video")
    elif choice == "3": download_with_ytdlp(url, a_dir, "audio")
    # ... etc
    return "back"

# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    show_banner()
    
    if not check_ffmpeg():
        console.print(Panel(
            "[warning]FFmpeg not found![/warning]\n"
            "You can still download videos up to 720p.\n"
            "To enable 1080p/4K and audio format conversion, please install FFmpeg and add it to your PATH.",
            border_style="yellow"
        ))
    
    if not SPOTIFY_AVAILABLE:
        console.print(Panel(
            "[info]Spotify Integration Available![/info]\n"
            "To enable Spotify features, install spotipy:\n"
            "[highlight]pip install spotipy[/highlight]",
            border_style="cyan"
        ))

    cfg = load_config()
    base_path = get_output_path(cfg)
    spotify_mgr = SpotifyManager(cfg)
    db = LibraryDB()
    failed_log = FailedLog()

    while True:
        console.print(Rule(style="blue"))
        console.print("  [bold yellow]1[/bold yellow]  Search YouTube")
        console.print("  [bold yellow]2[/bold yellow]  Paste URL")

        if SPOTIFY_AVAILABLE:
            console.print("  [bold yellow]3[/bold yellow]  Import from Spotify 🎵")
        else:
            console.print("  [dim]3  Import from Spotify (install spotipy)[/dim]")

        console.print("  [bold yellow]4[/bold yellow]  Settings")
        console.print("  [bold yellow]5[/bold yellow]  📊 Library Stats")

        if failed_log.count() > 0:
            console.print(f"  [bold yellow]6[/bold yellow]  ⟳  Retry Failed Downloads [warning]({failed_log.count()} pending)[/warning]")

        console.print("  [bold yellow]q[/bold yellow]  Quit")

        choice = Prompt.ask("[info]Main Menu[/info]", default="1").lower()

        if choice == "q":
            break
        elif choice == "1":
            search_flow(base_path)
        elif choice == "2":
            direct_url_flow(base_path)
        elif choice == "3":
            if SPOTIFY_AVAILABLE:
                spotify_import_flow(spotify_mgr, base_path, db=db, failed_log=failed_log)
            else:
                console.print("[warning]Install spotipy first: pip install spotipy[/warning]")
        elif choice == "4":
            settings_menu(cfg)
        elif choice == "5":
            show_stats_screen(db)
        elif choice == "6" and failed_log.count() > 0:
            retry_failed_downloads(failed_log, base_path, db)

    db.close()
    console.print("\n[muted]Goodbye.[/muted]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

