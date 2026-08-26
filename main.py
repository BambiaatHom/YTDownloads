import time
from datetime import timedelta, datetime
import os
import sys
import sqlite3
import subprocess
import os
import re
import shutil
import difflib
import json
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
import urllib.request
#3rd party libraries
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from rich import box
from rich.rule import Rule
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.theme import Theme
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, TextColumn, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.text import Text
from rich.panel import Panel
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False
try:
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
    from mutagen.flac import FLAC, Picture
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggvorbis import OggVorbis
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

CONFIG_FILE = Path.home() / ".ytdlp_config.json"
FAILED_LOG = Path.home() / ".ytdlp_failed.json"
DB_FILE = Path.home() / ".ytdlp_library.db"

# ── Theme & Console ──────────────────────────────────────────────────────────
console = Console ()
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

#--Library database----------------------------------------
class LibraryDB:
    """Handles the SQLite database for downloaded tracks and statistics."""
    def __init__(self):
        # DB_FILE is defined globally using Path.home() from our earlier adjustment
        self.conn = sqlite3.connect(str(DB_FILE))
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        """Initialize the tracking schemas inside SQLite if they don't exist yet."""
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                artist TEXT,
                album TEXT,
                youtube_url TEXT UNIQUE,
                file_path TEXT,
                format TEXT,
                file_size INTEGER,
                duration INTEGER,
                source TEXT,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def already_downloaded(self, title: str, artist: str = "") -> bool:
        """Check if a track matching this name and artist is already logged."""
        # Using a flexible text match so it catches duplicate audio downloads cleanly
        if artist:
            self.cursor.execute(
                "SELECT 1 FROM tracks WHERE LOWER(title) = LOWER(?) AND LOWER(artist) = LOWER(?)", 
                (title.strip(), artist.strip())
            )
        else:
            self.cursor.execute(
                "SELECT 1 FROM tracks WHERE LOWER(title) = LOWER(?)", 
                (title.strip(),)
            )
        return self.cursor.fetchone() is not None

    def add(self, title: str, artist: str, album: str, youtube_url: str, 
            file_path: str, fmt: str, file_size: int, source: str = "YouTube", duration: int = 0):
        """Add a new successfully downloaded track record to the database."""
        try:
            self.cursor.execute("""
                INSERT OR REPLACE INTO tracks 
                (title, artist, album, youtube_url, file_path, format, file_size, duration, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, artist, album, youtube_url, file_path, fmt, file_size, duration, source))
            self.conn.commit()
        except Exception as e:
            if 'console' in globals():
                console.print(f"[warning]⚠ Failed to log track to database: {e}[/warning]")

    def get_stats(self) -> dict:
        """Execute real database aggregations to feed your dashboard screen metrics."""
        try:
            # 1. Get total number of unique songs
            self.cursor.execute("SELECT COUNT(*) FROM tracks")
            total_tracks = self.cursor.fetchone()[0] or 0

            # 2. Get total cumulative data size in bytes
            self.cursor.execute("SELECT SUM(file_size) FROM tracks")
            total_size = self.cursor.fetchone()[0] or 0

            # 3. Get total runtime duration in seconds
            self.cursor.execute("SELECT SUM(duration) FROM tracks")
            total_duration = self.cursor.fetchone()[0] or 0

            # 4. Pull the 5 most recently saved tracks for your dashboard visual history feed
            self.cursor.execute("""
                SELECT title, artist, downloaded_at 
                FROM tracks 
                ORDER BY downloaded_at DESC 
                LIMIT 5
            """)
            raw_recent = self.cursor.fetchall()
            recent_tracks = [
                {"title": row[0], "artist": row[1], "date": row[2]} 
                for row in raw_recent
            ]

            # 5. Top artists by track count
            self.cursor.execute("""
                SELECT LOWER(artist), COUNT(*) 
                FROM tracks 
                WHERE artist IS NOT NULL AND artist != ''
                GROUP BY LOWER(artist)
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """)
            top_artists = self.cursor.fetchall()

            # 6. Track count grouped by format
            self.cursor.execute("""
                SELECT format, COUNT(*)
                FROM tracks
                WHERE format IS NOT NULL AND format != ''
                GROUP BY format
                ORDER BY COUNT(*) DESC
            """)
            by_format = self.cursor.fetchall()

        except Exception:
            # Safe fallbacks if database is brand new and completely empty
            total_tracks, total_size, total_duration = 0, 0, 0
            recent_tracks = []
            top_artists = []
            by_format = []

        return {
            "total_tracks": total_tracks,
            "total_size": total_size,
            "total_duration": total_duration,
            "recent": recent_tracks,
            "top_artists": top_artists,
            "by_format": by_format,
        }

    def close(self):
        """Close the database connection safely."""
        self.conn.close()

    ...
#--Failed download log----------------------------------------
class FailedLog:
    """Tracks failed downloads for later retry."""
    def __init__(self):
        self.path = FAILED_LOG
        self.entries = self._load()
    def _load(self):
        """Load failed entries from the log file."""
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return []
    def save(self):
        self.path.write_text(json.dumps(self.entries, indent=2))
    def add(self, youtube_url: str, reason: str, source: str = ""):
        """Add a failed download"""
        self.entries.append({"url": youtube_url, "reason": reason, "source": source})
        self.save()
    def clear(self):
        """Clear the failed log."""
        self.entries = []
        self.save()
    def count(self) -> int:
        return len(self.entries)
#--Youtube stuff----------------------------------------
def score_youtube_match(query: str, video_title: str, video_duration: int, expected_duration: int = None) -> Tuple[float, str]:
    score = 0.0
    reasons = []
    
    # Title similarity
    query_clean = re.sub(r'[^\w\s]', '', query.lower())
    title_clean = re.sub(r'[^\w\s]', '', video_title.lower())
    similarity = difflib.SequenceMatcher(None, query_clean, title_clean).ratio()
    score += similarity * 50
    reasons.append(f"title match {similarity:.0%}")
    
    # Penalise unwanted versions
    unwanted = ['cover', 'remix', 'live', 'karaoke', 'instrumental', 'tribute', 'reaction', 'nightcore']
    query_words = query.lower().split()
    for word in unwanted:
        if word in title_clean and word not in query_words:
            score -= 10
            reasons.append(f"-{word}")
            break
            
    # Duration match
    if expected_duration and expected_duration > 0:
        diff = abs(video_duration - expected_duration)
        if diff < 5: dur_score = 30
        elif diff < 15: dur_score = 20
        elif diff < 30: dur_score = 10
        elif diff < 60: dur_score = 5
        else: dur_score = 0
        score += dur_score
        reasons.append(f"duration ±{diff}s")
        
    # Bonus for official content
    official_terms = ['official', 'vevo', 'audio', 'lyrics', 'topic']
    for term in official_terms:
        if term in title_clean:
            score += 10
            reasons.append(f"+{term}")
            break
            
    score = max(0.0, min(100.0, score))
    return round(score, 1), ", ".join(reasons)
def find_best_youtube_match(query: str, artist: str = "", expected_duration: int = None,
                             max_results: int = 5) -> Tuple[Optional[dict], float, str]:
    """
    Search YouTube using yt-dlp and return the best matching video data dictionary.
    Returns (video_dict, score, reason).
    """
    # 1. Setup options to get metadata only without downloading files
    ydl_opts = {
        'default_search': f'ytsearch{max_results}', # Instructs yt-dlp to search
        'skip_download': True,                      # Do not download video files
        'quiet': True,                               # Suppress log spams
        'no_warnings': True,
        'extract_flat': True,                        # Quick extraction of search listings
        'impersonate': 'chrome', 
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'default', '-android_sdkless'],
                'player_skip': ['webpage', 'configs']
            }
        },
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            # 2. Extract information using the search string
            # If the user provided an artist name, we append it to target search relevance
            search_query = f"{artist} - {query}" if artist else query
            search_results = ydl.extract_info(search_query, download=False)
            
            # 3. Handle cases where no results are returned
            if not search_results or 'entries' not in search_results:
                return None, 0.0, "no results"
                
            videos = list(search_results['entries'])
            if not videos:
                return None, 0.0, "no results"

            best_video = None
            best_score = -1.0
            best_reason = ""

            # 4. Iterate over metadata dictionaries returned by yt-dlp
            for video in videos:
                try:
                    # yt-dlp uses dictionary keys like 'title' and 'duration'
                    title = video.get('title', '')
                    duration = int(video.get('duration') or 0)
                    
                    score, reason = score_youtube_match(
                        query, 
                        title,
                        duration,
                        expected_duration
                    )
                    
                    if score > best_score:
                        best_score = score
                        best_video = video
                        best_reason = reason
                except Exception:
                    continue

            return best_video, best_score, best_reason

    except Exception as e:
        return None, 0.0, str(e)
#-- config & setup----------------------------------------
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            # Opens, reads, parses, and closes in one go safely
            with CONFIG_FILE.open('r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError): 
            # Catching specific errors is better than a blind "except Exception"
            pass
    return {}
def save_config(cfg: dict) -> None:
    try:
        # Create the parent folders if they don't exist yet
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Write safely with explicit UTF-8 text encoding
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    except Exception as e:
        # Log or print the error so you know why saving failed
        print(f"Failed to save configuration: {e}")
def get_output_path(cfg: dict) -> str:
    """Return saved path or ask the user and save it."""
    #Quick check if it's already configured
    if "output_path" in cfg:
        # Convert to an absolute path string to ensure it's normalized
        return str(Path(cfg["output_path"]).resolve())

    console.print(Panel(
        "[info]Welcome![/info]\n"
        "Let's set a default folder for your downloads.\n"
        "[muted]We will create 'Videos' and 'Audio' subfolders automatically.[/muted]",
        border_style="cyan",
        padding=(1, 2),
    ))
    
    #Smart default cross-platform path mapping
    default_path = Path.home() / "Downloads" / "YTDownloads"
    
    while True:
        user_input = Prompt.ask("[info]Download folder[/info]", default=str(default_path))
        target_path = Path(user_input).expanduser().resolve()
        
        try:
            #Validation test: try creating the folder structure safely
            target_path.mkdir(parents=True, exist_ok=True)
            
            # If successful, store the clean string and break the loop
            path_str = str(target_path)
            cfg["output_path"] = path_str
            save_config(cfg)
            
            console.print(f"[success]✔  Saved and verified:[/success] [url]{path_str}[/url]\n")
            return path_str
            
        except Exception as e:
            # 4. Error notification loop if directory is restricted or invalid
            console.print(f"[danger]❌ Invalid path or permission denied:[/danger] {e}\n[muted]Please try a different directory.[/muted]\n")
def check_ffmpeg() -> bool:
    """Check if both FFmpeg and FFprobe are installed and accessible."""
    has_ffmpeg = shutil.which("ffmpeg") is not None
    has_ffprobe = shutil.which("ffprobe") is not None
    
    return has_ffmpeg and has_ffprobe
def get_subdirs(base: str | Path) -> tuple[Path, Path]:
    """Ensure Videos and Audio subdirectories exist and return them."""
    base_path = Path(base).resolve()
    
    v = base_path / "Videos"
    a = base_path / "Audio"
    
    try:
        v.mkdir(parents=True, exist_ok=True)
        a.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # If your program is running somewhere restricted, let yourself know why it failed
        print(f"Error: No permission to create folders inside {base_path}")
        raise
        
    return v, a
#--Spotify Intergration----------------------------------------
class  SpotifyManager:
    def __init__(self,cfg:dict):
        self.cfg = cfg
        self.sp = None
    def setup_spotify(self) -> bool:
        """Spotify authentification returns true if successful"""
        SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8118/callback"
        if not SPOTIFY_AVAILABLE:
            console.print("[danger]Spotify intergratoion is not available!")
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
        # 1. Check if credentials are already saved
        if "spotify_client_id" in self.cfg and "spotify_client_secret" in self.cfg:
            use_saved = Confirm.ask("[info]Use saved spotify credentials?[/info]", default=True)
            if use_saved:
                client_id = self.cfg["spotify_client_id"]
                client_secret = self.cfg["spotify_client_secret"]
            else:
                client_id = Prompt.ask("[info] Enter Spotify Client ID[/info]").strip()
                client_secret = Prompt.ask("[info] Enter Spotify Client Secret[/info]", password=True).strip()
                if Confirm.ask("[info]Save credentials for next time?[/info]", default=True):
                    self.cfg["spotify_client_id"] = client_id
                    self.cfg["spotify_client_secret"] = client_secret
                    save_config(self.cfg)
        else:
            # 2. FIXED: Added the missing fallback if no credentials exist yet
            client_id = Prompt.ask("[info] Enter Spotify Client ID[/info]").strip()
            client_secret = Prompt.ask("[info] Enter Spotify Client Secret[/info]", password=True).strip()
            if Confirm.ask("[info]Save credentials for next time?[/info]", default=True):
                self.cfg["spotify_client_id"] = client_id
                self.cfg["spotify_client_secret"] = client_secret
                save_config(self.cfg)

        # 3. FIXED: Pointing to the real Spotipy local cache file location
        local_cache = Path(".cache")
        if local_cache.exists():
            console.print(f"\n[warning]Found existing Spotify authentication cache[/warning]")
            if Confirm.ask("[info]Clear cache and re-authenticate? (Recommended if you're having permission issues)[/info]", default=False):
                try:
                    local_cache.unlink()
                    console.print(f"[success]Deleted: {local_cache.name}[/success]")
                except Exception as e:
                    console.print(f"[warning]Could not delete {local_cache.name}: {e}[/warning]")

        try:
            scope = "user-library-read playlist-read-private playlist-read-collaborative user-top-read user-follow-read"
            
            # 4. FIXED: Re-indented these lines back inside the try block
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=scope,
                open_browser=True,
                cache_path=str(local_cache) # Keeps the cache exactly where we expect it
            )
            
            # 5. FIXED: Changed 'spotify.Spotify' to 'spotipy.Spotify'
            self.sp = spotipy.Spotify(auth_manager=auth_manager) 
            
            # Test the connection
            user = self.sp.current_user()
            # Safety fallback in case a user does not have a public display name configured
            display_name = user.get('display_name', 'Unknown User') if user else 'Unknown User'
            console.print(f"[success]✔ Connected to Spotify as: {display_name}[/success]")
            
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
            console.print(f"2. Verify redirect URI is set to: {SPOTIFY_REDIRECT_URI}")
            console.print("3. Try clearing cache and re-authenticating")
            return False
    def get_liked_songs(self, limit: int = 50) -> list[dict]:
        """Fetch users liked songs"""
        if not self.sp:
            return[]
        songs = []
        with console.status("[info]Fetching liked songs...[/info]"):
            offset = 0
            #adjust request batchsize if limit is smallet than 50
            batch_size = min(50, limit)
            while len(songs) < limit:
                results = self.sp.current_user_saved_tracks(limit=batch_size, offset=offset)
                if not results or not results['items']:
                    break
                for item in results['items']:
                    #defensive parsing incase track data is missing or broken
                    if not item or not item.get('track'):
                        continue
                    track = item['track']
                    artist_name = track['artists'][0]['name'] if track.get('artists') else 'Unknown' 
                    songs.append({
                            'name': track.get('name', 'Unknown'),
                            'artist': artist_name,
                            'album': track.get('album', {}).get('name', 'Unknown'),
                            'search_query': f"{artist_name} - {track.get('name', 'Unknown')}",
                            'album_art_url': track.get('album', {}).get('images', [{}])[0].get('url', '')
                    })
                    #Exit if we hit end of libarary early
                    if not results.get('next'):
                        break
                    offset += batch_size
                    #clamp batch size for last loop iteration
                    batch_size = min(50, limit - len(songs))
        return songs[:limit]

    def get_playlists(self) -> list[dict]:
        """Fetch a clean summary list of all playlists available in the user's Spotify account."""
        if not self.sp:
            return []
        
        try:
            playlists = []
            results = self.sp.current_user_playlists(limit=50)
            
            while results:
                for pl in results.get('items', []):
                    if not pl:
                        continue
                    playlists.append({
                        "id": pl.get('id'),
                        "name": pl.get('name', 'Untitled Playlist'),
                        "track_count": pl.get('tracks', {}).get('total', 0)
                    })
                
                if results.get('next'):
                    results = self.sp.next(results)
                else:
                    break
                    
            return playlists
        except Exception as e:
            console.print(f"[danger]Failed to load Spotify playlist records: {e}[/danger]")
            return []

    def get_playlists_tracks(self, playlist_id: str) -> list[dict]:
        """Fetch users playlists"""
        if not self.sp:
            return[]
        songs = []
        try:
            with console.status("[info]Fetching playlist tracks...[/info]"):
                #1 - fetch metadta to display helpful context panel metrics
                try:
                    p_info = self.sp.playlist(playlist_id, fields='name,owner.display_name')
                    console.print(f"[dim]Playlist: {p_info.get('name', 'Unknown')} | Owner: {p_info.get('owner', {}).get('display_name', 'Unknown')}[/dim]")
                except Exception as e:
                    console.print(f"[warning]Could not fetch playlist metadata details: {e}[/warning]")
                #2 - loop continuously using playlist_name to extract tracks cleanly
                offset = 0
                batch_size = 100 #spotifys api allows 100 for playlists
                while True:
                    results = self.sp.playlist_items(
                        playlist_id,
                        limit=batch_size,
                        offset=offset,
                        fields='items(track(name,artists.name,album.name)),next'
                    )
                    if not results or not results.get('items'):
                        break
                    for item in results['items']:
                        if not item or not item.get('track'):
                            continue
                        track = item['track']
                        #Extra validation - filter out local files or empty track profiles
                        if not track.get('name'):
                            continue

                        artist_name = track['artists'][0]['name'] if track.get('artists') else 'Unknown'
                        album_data = track.get('album', {})
                        album_name = album_data.get('name', 'Unknown') if album_data else 'Unknown'
                        album_images = album_data.get('images', []) if album_data else []
                        art_url = album_images[0].get('url', '') if album_images else ''

                        songs.append({
                            'name': track['name'],
                            'artist': artist_name,
                            'album': track.get('album', {}).get('name', 'Unknown'),
                            'search_query': f"{artist_name} - {track['name']}",
                            'album_art_url': track.get('album', {}).get('images', [{}])[0].get('url', '')
                        })
                    if not results.get('next'):
                        break
                    offset += batch_size
                console.print(f"[success]✔ Fetched {len(songs)} tracks from playlist[/success]")
                return songs
        except Exception as e:
            console.print(f"[danger]Failed to fetch playlist tracks: {e}[/danger]")
            return []

#--formatting helper---------------------------------------
def fmt_duration(seconds) -> str:
    """Format seconds into human-readable hours, minutes, and seconds."""
    h, r = divmod(int(seconds or 0), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"
def fmt_views(n) -> str:
    """Format massive integers into clean engineering notations."""
    if n is None: return "N/A"
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)
def fmt_size(b) -> str:
    """Safely format raw data byte footprints into standard network units."""
    if b is None: return "?"
    # 1. Treat 0 bytes as a clean immediate exit point to avoid infinite loop iterations
    if b == 0: return "0.0 B" 
    # 2. Expanded units list past TB just to protect the boundary limits
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if b < 1024: 
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} EB"
def make_progress() -> Progress:
    """Construct a standardized framework instance for terminal loading bars."""
    return Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )
def nav_hint(options: list[str]) -> None:
    """Inject balanced menu help metrics inside operational tracking menus."""
    console.print(f"[nav]  ( {' · '.join(options)} )[/nav]")
def show_banner() -> None:
    """Display the application graphic header splash module."""
    banner = Text()
    banner.append("  ▶  ", style="bold red")
    banner.append("YouTube Downloader Pro", style="bold white")
    banner.append("  + Spotify", style="bold green")
    banner.append("  ▶  ", style="bold red")
    console.print(Panel(banner, style="bold blue", padding=(1, 4)))
#--Audio formatting and metadata embedding-----------------------------
def convert_audio(input_path: Path, output_format: str, bitrate: str = "320k") -> Path:
    """Convert audio file to specified format using FFmpeg safely."""
    # 1. FIXED: Added environmental guard check so it won't crash if FFmpeg is missing
    if not check_ffmpeg():
        console.print("[danger]FFmpeg not found! Cannot convert audio.[/danger]")
        return input_path
        
    output_path = input_path.with_suffix(f".{output_format}")
    if input_path == output_path:
        return input_path  # Already in the correct format!

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
        # 2. FIXED: Double-check path existence before trying to remove files to avoid OS errors
        if input_path.exists():
            os.remove(input_path)
        console.print(f"[success]✔ Converted to {output_format.upper()}[/success]")
        return output_path
    except Exception as e:
        print(f"[danger]Conversion failed: {e}[/danger]")
        return input_path
def normalize_audio(file_path: Path) -> None:
    """Normalize audio volume using FFmpeg loudnorm filter."""
    if not check_ffmpeg() or not file_path.exists():
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
    """Embed ID3 metadata tags into audio file safely."""
    if not MUTAGEN_AVAILABLE or not file_path.exists():
        console.print("[warning]Tagging skipped: Mutagen library not available or file missing[/warning]")
        return

    try:
        suffix = file_path.suffix.lower()
        art_data = None
        
        # 3. FIXED: Fetch album art bytes with explicit headers to avoid HTTP 403 Forbidden blockers
        if album_art_url:
            try:
                req = urllib.request.Request(
                    album_art_url, 
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    art_data = r.read()
            except Exception:
                pass # If artwork download fails, we still want to embed text tags!

        if suffix == ".mp3":
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                tags = ID3()
            tags["TIT2"] = TIT2(encoding=3, text=title)
            tags["TPE1"] = TPE1(encoding=3, text=artist)
            tags["TALB"] = TALB(encoding=3, text=album)
            if art_data:
                tags["APIC"] = APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art_data)
            tags.save(str(file_path))

        elif suffix == ".flac":
            audio = FLAC(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            if art_data:
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
                audio["covr"] = [MP4Cover(art_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()

        elif suffix == ".ogg":
            audio = OggVorbis(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            audio.save()

        console.print("[success]✔ Metadata tags embedded[/success]")

    except Exception as e:
        console.print(f"[warning]Tag embedding skipped: {e}[/warning]")
#--Core Download Logic----------------------------------------
def download_video_merged(url: str, out_dir: Path) -> Optional[Path]:
    """Downloads high quality video, audio tracks seporately, then merges natively using FFmpeg"""
    if not check_ffmpeg():
        console.print("[danger]Error: FFmpeg required for high quality video formats![/danger]")
        return None
    outtmpl_str = str(out_dir / '%(title)s.%(ext)s')

    #yt-dlp layout config options
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',  # Grabs best visual and best audio streams
        'merge_output_format': 'mp4',           # Enforces merging into standard MP4
        'outtmpl': outtmpl_str,
        'restrictfilenames': True,
        'quiet': True,
        'no_warnings': True,
        'no_colour': True,
        'compat_opts': {'no-youtube-channel-redirect'},
        'impersonate': 'chrome', 
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'default', '-android_sdkless'],
                'player_skip': ['webpage', 'configs']
            }
        },
    }
    cookie_path = Path.cwd() / ".youtube_cookies.txt"
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(cookie_path)
    try:
        console.print(f"[info]Downloading high quality video...[/info]")
        with YoutubeDL(ydl_opts) as ydl:
            #extract metadata and download
            info = ydl.extract_info(url, download=True)
            if not info or 'title' not in info:
                raise ValueError("Incomplete video information extracted.")
            final_file = Path(ydl.prepare_filename(info)).with_suffix('.mp4')
            console.print(f"[success]✔ Downloaded and merged: {final_file.name}[/success]")
            return final_file
    except Exception as e:
        console.print(f"[danger]Error during video download merge pipeline: {e}[/danger]")
        return None
    
def download_audio_format(video_url: str, out_dir: Path, format_choice: str,
                          song_meta: dict = None, db: LibraryDB = None,
                          normalize: bool = False, yes_all: bool = False) -> Optional[Path]:
    """Downloads audio track natively using yt-dlp and converts format."""
    
    # 1. Map extension choices strings
    fmt_map = {"1": "mp3", "2": "mp3", "3": "wav", "4": "flac", "5": "ogg", "6": "m4a"}
    fmt = fmt_map.get(format_choice, "mp3")
    bitrate = "192" if format_choice == "2" else "320"

    audio_dir = out_dir / "Audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 2. Setup your native core options dictionary using your central cookie file
    cookie_path = Path.cwd() / ".youtube_cookies.txt"
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(audio_dir / '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': fmt,
            'preferredquality': bitrate,
        }],
        'impersonate': 'chrome', 
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'default', '-android_sdkless'],
                'player_skip': ['webpage', 'configs']
            }
        },
    }
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        ydl_opts['cookiefile'] = str(cookie_path)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            # 3. Extract the live network dictionary payload and download the file
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            duration = info.get('duration', 0)
            
            # Determine the exact disk path string after conversion completes
            final_path = Path(filename).with_suffix(f".{fmt}")

        # ◄ THE FIX: Dynamically populate titles from YouTube if Spotify data is absent
        if song_meta:
            title = song_meta.get("name", "Unknown Title")
            artist = song_meta.get("artist", "Unknown Artist")
            album = song_meta.get("album", "")
            art_url = song_meta.get("album_art_url", "")
        else:
            # Slicing native yt-dlp key metrics cleanly
            title = info.get('title', 'Unknown Title') or "Unknown Title"
            artist = info.get('uploader', info.get('artist', 'Unknown Artist')) or "Unknown Artist"
            album = info.get('album', '') or ""
            art_url = info.get('thumbnail', '') or ""

        # 4. Check for database log updates or duplicates
        if db and db.already_downloaded(title, artist):
            if not yes_all:
                if not Confirm.ask(f"[warning]'{title}' already downloaded. Keep duplicate?[/warning]", default=False):
                    console.print("[muted]Skipped (duplicate)[/muted]")
                    if final_path.exists(): final_path.unlink() # Delete the fresh file if they cancel
                    return None

        # Print visual info card panel
        console.print(Panel(f"[highlight]{title}[/highlight]\n[muted]Artist:[/muted] {artist}", border_style="green"))

        # 5. Execute downstream postprocessing extensions safely
        if normalize:
            normalize_audio(final_path)

        if MUTAGEN_AVAILABLE:
            embed_tags(final_path, title, artist, album, art_url)

        if db:
            db.add(title, artist, album, video_url, str(final_path), fmt, final_path.stat().st_size, "YouTube", duration)

        return final_path
    except Exception as e:
        console.print(f"[danger]Download failed: {e}[/danger]")
        return None

#--gett cookies------------------------
def sync_browser_cookies(browser_name: str = "brave") -> Optional[str]:
    """
    Extract active session cookies from your browser and dump them 
    safely into a text file inside your project workspace.
    """
    cookie_file_path = Path.cwd() / ".youtube_cookies.txt"
    
    # Configure yt-dlp to extract cookies from your browser and save them locally
    ydl_opts = {
        'cookiesfrombrowser': [browser_name],
        'cookiefile': str(cookie_file_path),
        'quiet': True,
        'no_warnings': True,
    }
    
    with console.status(f"[info]Syncing active authorization from {browser_name.upper()}...[/info]"):
        try:
            # We fire an empty dummy lookup on a basic test page to force yt-dlp to dump the cookiefile
            with YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info("https://youtube.com", download=False)
                
            if cookie_file_path.exists() and cookie_file_path.stat().st_size > 0:
                console.print(f"[success]✔ Successfully generated local authentication file: {cookie_file_path.name}[/success]")
                return str(cookie_file_path)
        except Exception as e:
            console.print(f"[warning]⚠ Could not sync browser cookies automatically: {e}[/warning]")
            console.print("[muted]Downstream loops will fallback to unauthenticated guest mode.[/muted]")
            
    # Return None if it fails so your program doesn't crash on boot
    return None

#-- UI --------------------------------
def format_selection_menu(video_data: dict, out_dir: Path,song_meta: dict = None) -> None:
    """interactive menu to choose export settings."""
    if not video_data:
        console.print("[danger]Error: No video data provided for download.[/danger]")
        return
    console.print(Rule("[info]Audio format selection[/info]"))
    console.print(Panel(
        "Choose your preffered audio format and quality settings below.",
        border_style="cyan"
    ))
    has_ffmpeg = check_ffmpeg()
    console.print("\n[info]Available Formats:[/info]")
    console.print("  [bold yellow]1[/bold yellow]  MP3 320kbps (High Quality)")
    console.print("  [bold yellow]2[/bold yellow]  MP3 192kbps (Standard Quality)")
    console.print("  [bold yellow]3[/bold yellow]  WAV (Lossless uncompressed)")
    console.print("  [bold yellow]4[/bold yellow]  FLAC (Lossless compressed)")
    console.print("  [bold yellow]5[/bold yellow]  OGG Vorbis")
    console.print("  [bold yellow]6[/bold yellow]  M4A/AAC 320kbps (Original YouTube quality)")
    if not has_ffmpeg:
        console.print("\n[warning]⚠ FFmpeg required for formats 1-5[/warning]")
        console.print("[muted]Only M4A available without FFmpeg[/muted]")
    console.print("\n  [bold yellow]b[/bold yellow]  Back")
    nav_hint(["1-6 = format", "b = back"])
    while True:
        choice = Prompt.ask("[info]Choose format[/info]", default="b").strip().lower()
        if choice == "b":
            return
        if choice in ["1", "2", "3", "4", "5"] and not has_ffmpeg:
            console.print("[danger]❌ FFmpeg is missing! Please choose option 6 or install FFmpeg.[/danger]")
            continue
        break
    video_url = video_data.get('url') or video_data.get('webpage_url')
    download_audio_format(
        video_url=video_url, 
        out_dir=out_dir, 
        format_choice=choice, 
        song_meta=song_meta
    )
def settings_menu(cfg: dict) -> str:
    while True:
        curr = cfg.get("output_path")
        console.print(Rule("Settings"))
        console.print(f"Current Path: [url]{curr}[/url]")
        console.print("  [bold yellow]1[/bold yellow]  Change Path")
        console.print("  [bold yellow]2[/bold yellow]  Reset Spotify Credentials")
        console.print("  [bold yellow]3[/bold yellow]  Clear Spotify Cache (Fix Permission Issues)")
        console.print("  [bold yellow]b[/bold yellow]  Back")

        choice = Prompt.ask("Choice", default="b").strip().lower()
        
        # ◄ NATIVE FIX: Explicitly handle backing out gracefully
        if choice == "b":
            return "back"
            
        elif choice == "1":
            new_p = Prompt.ask("New Path").strip()
            cfg["output_path"] = str(Path(new_p).expanduser().resolve())
            save_config(cfg)
            console.print("[success]Saved![/success]")
        elif choice == "2":
            cfg.pop("spotify_client_id", None)
            cfg.pop("spotify_client_secret", None)
            save_config(cfg)
            console.print("[success]Spotify credentials cleared![/success]")
        elif choice == "3":
            clear_spotify_cache()
        else:
            # Safe catch-all for typos so they stay inside the menu loop
            console.print("[warning]Invalid selection[/warning]")

def show_stats_screen(db: Any) -> None:
    """Display download library stats from the tracking database cleanly."""
    if not db:
        console.print("[warning]No database connection available to pull stats.[/warning]")
        Prompt.ask("\n[muted]Press Enter to go back[/muted]", default="")
        return

    try:
        stats = db.get_stats()
    except Exception as e:
        console.print(f"[danger]Failed to load stats from database: {e}[/danger]")
        Prompt.ask("\n[muted]Press Enter to go back[/muted]", default="")
        return

    console.print(Rule("[info]📊 Download Library Stats[/info]"))

    # 1. Overview panel configuration layout handles
    total_tracks = stats.get("total_tracks", 0)
    total_bytes = stats.get("total_size", 0) or 0
    size_str = fmt_size(total_bytes)
    
    console.print(Panel(
        f"[highlight]Total Downloaded:[/highlight] {total_tracks} tracks\n"
        f"[highlight]Total Size:[/highlight] {size_str}",
        border_style="cyan"
    ))

    # 2. Top artists display table profile
    top_artists = stats.get("top_artists", [])
    if top_artists:
        t = Table(title="Top Artists", box=box.SIMPLE)
        t.add_column("Artist", style="cyan")
        t.add_column("Tracks", style="yellow", justify="right") # Right alignment looks cleaner for counts
        for artist, count in top_artists:
            t.add_row(artist or "Unknown", str(count))
        console.print(t)

    # 3. Code format breakdown tracker dashboard
    by_format = stats.get("by_format", [])
    if by_format:
        t = Table(title="By Format", box=box.SIMPLE)
        t.add_column("Format", style="green")
        t.add_column("Tracks", style="yellow", justify="right")
        for fmt, count in by_format:
            t.add_row((fmt or "?").upper(), str(count))
        console.print(t)

    # 4. Recent tracks lookup sequence
    recent = stats.get("recent", [])
    if recent:
        t = Table(title="Recently Downloaded", box=box.SIMPLE)
        t.add_column("Title", style="white", max_width=40) # Hard boundary limits avoid layout breakages
        t.add_column("Artist", style="cyan", max_width=30)
        t.add_column("When", style="dim")
        
        for item in recent:
            title = item.get('title', 'Unknown Track')
            artist = item.get('artist', 'Unknown Artist')
            when = item.get('date', '')
            if not when:
                when_str = "Unknown Date"
            else:
                try:
                    dt = datetime.fromisoformat(str(when))
                    when_str = dt.strftime("%d %b %Y %H:%M")
                except Exception:
                    when_str = str(when)[:16]

            # Safe text truncation checks to ensure UI stays perfectly inline
            clean_title = str(title or "Unknown Track")
            clean_artist = str(artist or "Unknown Artist")
            t.add_row(clean_title, clean_artist, when_str)
        console.print(t)

    # Simple layout blocker to freeze frame the data views
    Prompt.ask("\n[muted]Press Enter to go back[/muted]", default="")

def show_native_metadata(info_dict: Dict) -> None:
    """display details using native yt-dlp metadata"""
    title = info_dict.get('title', 'Unknown Title')
    uploader = info_dict.get('uploader', 'Unknown Artist')
    duration = info_dict.get('duration', 0)
    views = info_dict.get('view_count', 0)
    #yt-dlp output dates are strings, so we slice em to be DD/MM/YYYY
    date_str = info_dict.get('upload_date', '')
    formatted_date = "N/A"
    if date_str and len(date_str) == 8:
        try:
            formatted_date = f"{date_str[6:8]}/{date_str[4:6]}/{date_str[0:4]}"
        except Exception:
            pass
        info_panel = (
            f"[bold white]Title:[/]      [highlight]{title}[/]\n"
            f"[bold white]Author:[/]     [info]{uploader}[/]\n"
            f"[bold white]Duration:[/]   {fmt_duration(duration)}\n"
            f"[bold white]Views:[/]      {views:,}\n"
            f"[bold white]Date:[/]       {formatted_date}"
        )
        console.print(Panel(info_panel, title="[title] Video info[/title]", border_style="blue"))
def video_menu(info_dict: dict, base_path: str, db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    """Video menu using yt-dlp dictionary data"""
    v_dir, a_dir = get_subdirs(base_path)
    has_ffmpeg  = check_ffmpeg()
    
    # ◄ CRITICAL FIX: Changed 'webpage_irl' to 'webpage_url'
    video_url = info_dict.get('webpage_url')
    
    # Cookie path guard for all option blocks
    cookie_path = Path.cwd() / ".youtube_cookies.txt"
    has_cookies = cookie_path.exists() and cookie_path.stat().st_size > 0

    while True:
        console.print(Rule(style="cyan"))
        show_native_metadata(info_dict)
        console.print("[info]Actions:[/info]")
        if has_ffmpeg:
            console.print("  [bold yellow]1[/bold yellow]  Best Quality Video (1080p/4K + Audio Merge)")
        else:
            console.print("  [dim]1  Best Quality (Requires FFmpeg - Not Found)[/dim]")

        console.print("  [bold yellow]2[/bold yellow]  Standard Video (720p max)")
        console.print("  [bold yellow]3[/bold yellow]  Audio Only (.m4a)")
        console.print("  [bold yellow]4[/bold yellow]  Download Captions")
        console.print("  [bold yellow]5[/bold yellow]  Audio Formats (MP3/WAV/FLAC)")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        console.print("  [bold yellow]q[/bold yellow]  Quit")
        choice = Prompt.ask("[info]Choice:[/info]")
        if choice == "q": return "quit"
        if choice == "b": return "back"
        
        if choice == "1":
            if has_ffmpeg:
                # Forward arguments down into the native high-res pipeline
                # (You may want to update download_video_merged's definition to accept db/failed_log later)
                download_video_merged(video_url, v_dir)
            else:
                console.print("[warning]FFmpeg not found. Install it to enable 1080p/4K.[/warning]")
                continue
                
        elif choice == "2":
            ydl_opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best',
                'merge_output_format': 'mp4',
                'outtmpl': str(v_dir / '%(title)s.%(ext)s'),
                'quiet': True,
                'impersonate': 'chrome', 
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'default', '-android_sdkless'],
                        'player_skip': ['webpage', 'configs']
                    }
                },
            }
            if has_cookies: ydl_opts['cookiefile'] = str(cookie_path)
            
            with console.status("[bold cyan]Download standard 720p video", spinner="dots"):
                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                    console.print("[success]✔ Standard video downloaded successfully![/success]")
                    
                    # ◄ DB HOOK PLACEHOLDER: Save successful history item
                    # if db: db.add_track(info_dict.get('title'), info_dict.get('id'), "video")
                except Exception as e:
                    console.print(f"[danger]Download failed: {e}[/danger]")
                    # ◄ FAILED LOG PLACEHOLDER: Save failed item
                    # if failed_log: failed_log.add(video_url, str(e))
                    
        elif choice == "3":
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio',
                'outtmpl': str(a_dir / '%(title)s.%(ext)s'),
                'quiet': True,
                'impersonate': 'chrome', 
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'default', '-android_sdkless'],
                        'player_skip': ['webpage', 'configs']
                    }
                },
            }
            if has_cookies: ydl_opts['cookiefile'] = str(cookie_path)
            
            with console.status("[bold cyan]Extracting M4A audio...", spinner="dots"):
                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                    console.print("[success]✔ Audio track saved successfully![/success]")
                    # if db: db.add_track(info_dict.get('title'), info_dict.get('id'), "audio")
                except Exception as e:
                    console.print(f"[danger]Download failed: {e}[/danger]")
                    # if failed_log: failed_log.add(video_url, str(e))
                    
        elif choice == "4":
            ydl_opts = {
                'writesubtitles': True,
                'allsubtitles': True,
                'skip_download': True, 
                'outtmpl': str(v_dir / '%(title)s.%(ext)s'),
                'quiet': True,
                'impersonate': 'chrome', 
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'default', '-android_sdkless'],
                        'player_skip': ['webpage', 'configs']
                    }
                },
            }
            if has_cookies: ydl_opts['cookiefile'] = str(cookie_path)
            
            with console.status("[bold cyan]Fetching captions/subtitles/lyrics...", spinner="dots"):
                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
                    console.print("[success]✔ Subtitles saved into your videos folder![/success]")
                except Exception as e:
                    console.print(f"[danger]Failed to extract captions: {e}[/danger]")
                    
        elif choice == "5":
            format_selection_menu(video_data=info_dict, out_dir=a_dir)
            
        if Confirm.ask("[info]Perform another action on this video?[/info]", default=False):
            continue
        return "back"

def clear_spotify_cache() -> None:
    """Clear all Spotify authentication cache files safely and completely."""
    console.print(Rule("[warning]Clear Spotify Cache[/warning]"))
    console.print("[info]This will force you to re-authenticate with Spotify.[/info]")
    console.print("[info]Use this if you're having permission issues with playlists.[/info]\n")
    
    if not Confirm.ask("[warning]Are you sure?[/warning]", default=False):
        return
    
    # ◄ FIXED: Added Path.cwd() to target the current working directory where spotipy defaults its tokens!
    cache_locations = [
        Path.cwd(),
        Path.home(),
        Path.home() / ".cache",
        Path.home() / ".spotify_cache",
    ]
    
    deleted_count = 0
    username = os.getenv("USER", "user")
    
    for cache_dir in cache_locations:
        if cache_dir.exists() and cache_dir.is_dir():
            spotify_files = []
            
            # ◄ FIXED: Target explicit spotipy authentication string patterns safely
            spotify_files.extend(list(cache_dir.glob("*spotify*")))
            spotify_files.extend(list(cache_dir.glob(".spotify*")))
            spotify_files.extend(list(cache_dir.glob(f".cache-{username}*")))
            
            # Explicit look for the raw hidden fallback token file inside the project workspace folder
            if cache_dir == Path.cwd():
                local_dot_cache = Path.cwd() / ".cache"
                if local_dot_cache.exists() and local_dot_cache.is_file():
                    spotify_files.append(local_dot_cache)
            
            for cache_file in spotify_files:
                try:
                    if cache_file.is_file():
                        cache_file.unlink()
                        console.print(f"[success]✔ Deleted cache token file: {cache_file.name}[/success]")
                        deleted_count += 1
                    elif cache_file.is_dir():
                        shutil.rmtree(cache_file)
                        console.print(f"[success]✔ Deleted cache folder: {cache_file.name}[/success]")
                        deleted_count += 1
                except Exception as e:
                    console.print(f"[warning]Could not delete {cache_file.name}: {e}[/warning]")
    
    if deleted_count > 0:
        console.print(f"\n[success]✔ Cleared {deleted_count} cache item(s) successfully![/success]")
        console.print("[info]Next time you use Spotify features, you will need to re-authenticate via your browser.[/info]")
        console.print("[highlight]This should fix any stuck authorization or playlist permission blocks![/highlight]\n")
    else:
        console.print("\n[info]No active Spotify authentication cache keys found on this laptop.[/info]\n")

######DOWNLOAD ENGINESSS#####
#--Bulk downloading------------------
def bulk_download_songs(songs: List[Dict], base_path: str, org_choice: str, 
                        format_choice: str, folder_name: str, 
                        db: LibraryDB = None, failed_log: FailedLog = None,
                        normalize: bool = False, yes_all: bool = False,
                        min_confidence = 40.0) -> None:
    """
    Reusable bulk download engine with:
    - ETA tracking
    - Confidence score display
    - Duplicate detection
    - Failed log
    - Desktop notification on finish
    """
    v_dir, a_dir = get_subdirs(base_path)
    total = len(songs)
    successful = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    console.print(f"\n[info]Starting bulk download of {total} tracks...[/info]")
    for i, song in enumerate(songs, 1):
        #eta calc
        elapsed = time.time() - start_time
        if i > 1:
            avg_per_track = elapsed / (i -1)
            remaining_tracks = total - (i - 1)
            eta_secs = avg_per_track * remaining_tracks
            eta_str = str(timedelta(seconds=int(eta_secs)))
        else:
            eta_str = "calculating..."
        console.print(Rule(
            f"[dim]{i}/{total}  ETA: {eta_str} - {song.get('search_query', song.get('name','?'))[:50]}[/dim]"
        ))
        #determine output folder
        def safe(s): return "".join(c for c  in s if c.isalnum() or c in " -_").strip()
        if org_choice == "1":
            output_folder = a_dir / safe(folder_name)
        elif org_choice == "2":
            output_folder = a_dir / safe(song.get("artist", "Unknown"))
        elif org_choice == "3":
            output_folder = a_dir / safe(song.get("album", "Unknown"))
        else: #"4"
            output_folder = a_dir / safe(song.get("artist", "Unknown")) / safe(song.get("album", folder_name))
        output_folder.mkdir(parents=True, exist_ok=True)
        #Youtbe match with confidence
        query = song.get("search_query", song.get("name", ""))
        expected_dur = song.get("duration_ms", 0) // 1000 if song.get("duration_ms") else None
        video, confidence, reason = find_best_youtube_match(
            query, song.get("artist", ""), expected_dur
        )
        if not video:
            console.print(f"[danger]✘ No YouTube match for: {query}[/danger]")
            if failed_log:
                failed_log.add(query, "no youtube match", "bulk")
            failed += 1
            continue
        # ◄ FIX: Extracted title via dict get() safely to handle the yt-dlp backend
        v_title = video.get('title', 'Unknown Title')
        conf_colour = "green" if confidence >= 70 else "yellow" if confidence >= 40 else "red"
        console.print(f"  [dim]Match:[/dim] {v_title[:55]} [{conf_colour}]{confidence}% confidence[/{conf_colour}]  [dim]({reason})[/dim]")
        if confidence < min_confidence:
            console.print(f"  [warning]Confidence too low ({confidence}% < {min_confidence}%), skipping[/warning]")
            if failed_log:
                failed_log.add(query, f"low confidence: {confidence}%", "bulk")
            skipped += 1
            continue
        #--download--------
        try:
            video_id = video.get('id')
            if not video_id:
                console.print(f"  [danger]✘ Could not retrieve video ID for: {v_title}[/danger]")
                continue
            watch_url = f"https://youtube.com/watch?v={video_id}"
            result = download_audio_format(
                watch_url, output_folder, format_choice,
                song_meta=song, db=db,
                normalize=normalize, yes_all=yes_all
            )
            if result:
                successful += 1
            else:
                skipped += 1
        except Exception as e:
            console.print(f"  [danger]✘ Download failed: {e}[/danger]")
            if failed_log:
                failed_log.add(query, str(e), "bulk")
            failed += 1
    #sumamry
    total_time = str(timedelta(seconds=int(time.time() - start_time)))
    console.print(Panel(
        f"[success]✔ Downloaded:  {successful}[/success]\n"
        f"[warning]⟳ Skipped:    {skipped}[/warning]\n"
        f"[danger]✘ Failed:     {failed}[/danger]\n"
        f"[muted]⏱ Time:       {total_time}[/muted]",
        title="Bulk Download Complete",
        border_style="green"
    ))
    if failed_log and failed_log.count() > 0:
        console.print(f"[muted]{failed_log.count()} total failed downloads saved to {FAILED_LOG}[/muted]")

def get_bulk_options() -> Tuple[str, bool, bool, float]:
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

def retry_failed_downloads(failed_log: FailedLog, base_path:str, db: LibraryDB) -> None:
    """Retry all failed downloads"""
    entries = failed_log.entries
    if not entries:
        console.print("[info]No failed downloads to retry :) ![/info]")
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
    retry_folder = a_dir / "Retired Downloads"
    retry_folder.mkdir(parents=True, exist_ok=True)
    bulk_download_songs(songs, base_path, "1", format_choice, "Retired Downloads",
                        db=db, failed_log=failed_log,
                        normalize=normalize, yes_all=yes_all, min_confidence=min_conf)
    
def spotify_import_flow(spotify_mgr: SpotifyManager, base_path: str,
                        db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    """main menu for spotify import options"""
    if not spotify_mgr.sp:
        if not spotify_mgr.setup_spotify():
            return "back"
    while True:
        console.print(Rule(style="green"))
        console.print("[info]Spotify Import Options:[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Import Liked Songs")
        console.print("  [bold yellow]2[/bold yellow]  Import from Playlist")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        
        choice = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()

        if choice == "b":
            return "back"
        elif choice == "1":
            import_liked_songs(spotify_mgr, base_path, db=db, failed_log=failed_log)
        elif choice == "2":
            import_playlist(spotify_mgr, base_path, db=db, failed_log=failed_log)
def import_liked_songs(spotify_mgr: SpotifyManager, base_path: str,
                       db: LibraryDB = None, failed_log: FailedLog =None) -> None:
    """import and download users liked songs from spotify"""
    if Confirm.ask("[info]Download ALL liked songs?[/info]", default=True):
        limit = 99999
    else:
        limit = int(Prompt.ask("[info]How many liked songs to import?[/info]", default="50"))
    songs = spotify_mgr.get_liked_songs(limit=limit)
    if not songs:
        console.print("[warning]No liked songs found![/warning]")
        return
    console.print(f"[success]Found {len(songs)} liked songs![/success]\n")
    table = Table(title="Preview (First 10)", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Artist", style="cyan")
    table.add_column("Song", style="white")
    table.add_column("Album", style="dim")

    for i, song in enumerate(songs[:10], 1):
        table.add_row(str(i), song['artist'], song['name'], song.get('album','')[:30])
    console.print(table)
    if not Confirm.ask(f"\n[info]Download all {len(songs)} songs?[/info]", default=True):
        return
    console.print("\n[info]How should files be organized?[/info]")
    console.print("  [bold yellow]1[/bold yellow]  All in one folder (Liked Songs)")
    console.print("  [bold yellow]2[/bold yellow]  By Artist")
    console.print("  [bold yellow]3[/bold yellow]  By Album")
    console.print("  [bold yellow]4[/bold yellow]  By Artist/Album")
    org_choice = Prompt.ask("[info]Organization[/info]", choices=["1","2","3","4"], default="1")

    format_choice, normalize, yes_all, min_conf = get_bulk_options()
    if not format_choice:
        return
    bulk_download_songs(songs, base_path, org_choice, format_choice,
                        "Liked Songs", db=db, failed_log=failed_log,
                        normalize=normalize, yes_all=yes_all, min_confidence=min_conf)
def import_playlist (spotify_mgr: SpotifyManager, base_path: str,
                     db: LibraryDB = None, failed_log: FailedLog = None) -> None:
    """import songs from spotigy playlists"""
    playlists = spotify_mgr.get_playlists()#
    if not playlists:
        console.print("[warning]No playlists found![/warning]")
        return
    table = Table("Your Playlists", box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Name", style="white")
    table.add_column("Tracks", style="dim")

    for i, pl in enumerate(playlists, 1):
        table.add_row(str(i), pl['name'], str(pl['track_count']))
    console.print(table)
    console.print("\n[muted]Note: Some playlists maybe private/restricted and wont be accessible.[/muted]")
    choice = Prompt.ask("[info]Select playlist #[/info]", default="b")
    if choice.lower() == "b":
        return
    if not choice.isdigit() or not (1 <= int(choice) <= len(playlists)):
        console.print("[warning]Invalid selection[/warning]")
        return
    selected_playlist = playlists[int(choice) - 1]
    songs = spotify_mgr.get_playlist_tracks(selected_playlist['id'])
    # if fails or return empty, offer youtube search workaround
    if not songs or len(songs) == 0:
        console.print(f"\n[warning]Could not fetch tracks from Spotify API[/warning]")
        console.print("[info]This is a known Spotify API bug with some playlists.[/info]\n")
        console.print("[highlight]Workaround Options:[/highlight]")
        console.print("  [bold yellow]1[/bold yellow]  Try a different playlist")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        fail_choice = Prompt.ask("[info]Choice[/info]", choices=["1", "b"], default="b")
        
        if fail_choice == "1":
            # Loops cleanly back to the top of import_playlist to try a different selection
            return import_playlist(spotify_mgr, base_path, db=db, failed_log=failed_log)
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
    
    # ◄ FIX: Use our central bulk configuration options checker (4 variables)
    format_choice, normalize, yes_all, min_conf = get_bulk_options()
    if not format_choice:
        return
    
    # ◄ FIX: Offload the entire loop to bulk_download_songs. It handles the
    # ETA calculations, duplicate checking, yt-dlp search matching, and notifications.
    bulk_download_songs(
        songs, base_path, org_choice, format_choice,
        folder_name=selected_playlist['name'],
        db=db, failed_log=failed_log,
        normalize=normalize, yes_all=yes_all, min_confidence=min_conf
    )
#--Format choice--------------------------
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
    """Search with a pre-filled query using yt-dlp."""
    ydl_opts = {
        'playlist_items': '1-10',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    with console.status(f"[info]Searching for: {query}[/info]"):
        try:
            with YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"ytsearch20:{query}", download=False)
            
            # Extract flat dictionary array from yt-dlp payload
            top_vids = result.get('entries', []) if result else []
        except Exception as e:
            console.print(f"[danger]Search failed: {e}[/danger]")
            return "back"

    if not top_vids:
        console.print("[warning]No results found.[/warning]")
        return "back"

    # Results Table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
    table.add_column("#", style="yellow", width=4)
    table.add_column("Title", style="white")
    table.add_column("Length", style="dim")

    for i, v in enumerate(top_vids, 1):
        v_title = v.get('title', 'Unknown Title')
        duration = v.get('duration') # yt-dlp returns seconds
        length_str = fmt_duration(duration) if duration else "--:--"
        table.add_row(str(i), v_title, length_str)

    console.print(table)

    c = Prompt.ask("[info]Pick #[/info]", default="b")
    if c.lower() == "b":
        return "back"
    if c.isdigit() and 1 <= int(c) <= len(top_vids):
        # ◄ FIX: Pull the flat video hash ID to reassemble the destination path URL
        selected_vid = top_vids[int(c) - 1]
        url = selected_vid.get('url') or selected_vid.get('webpage_url')

        if not url:
            v_id = selected_vid.get('id')
            console.print("[danger]Could not extract video ID.[/danger]")
            return "back"
            
        return load_and_route(url, base_path)
    
    return "back"
#--Search flows-------------------
def search_flow(base_path: str, db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    """Natively search YouTube using yt-dlp and display with Rich Table"""
    while True:
        console.print(Rule(style="cyan"))
        query = Prompt.ask("[info]Search[/info] (or 'b' back)", default="b").strip()
        if query.lower() == "b": return "back"
        if query.lower() == "q": return "quit"
        ydl_opts = {
            'playlist_items': '1-10',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        with console.status("[info]Searching...[/info]"):
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    result = ydl.extract_info(f"ytsearch20:{query}", download=False)
                top_vids = result.get('entries', []) if result else []
            except Exception as e:
                console.print("[danger]Search failed: {e}[/danger]")
                continue
        if not top_vids:
            console.print("[warning]No results found[/warning]")
            continue
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("#", style="yellow", width=4)
        table.add_column("Title", style="white")
        table.add_column("Length", style="dim")
        #loops through the list of video dictionaries
        for i, v in enumerate(top_vids, 1):
            title = v.get('title', 'Unknow Title')
            duration = v.get('duration')
            lenght_str = fmt_duration(duration) if duration else "--:--"
            table.add_row(str(i), title, lenght_str)
        console.print(table)
        c = Prompt.ask("[info]Pick #[/info]", default="b")
        if c.lower() == "b": continue
        if c.isdigit() and 1 <= int(c) <= len(top_vids):
            selected_vid = top_vids[int(c) - 1]
            url = selected_vid.get('url') or selected_vid.get('webpage_url')
            if not url:
                # If both fallback looks fail, reassemble cleanly via the base video token code
                v_id = selected_vid.get('id')
                if v_id and len(v_id) == 11: # Standard YouTube IDs are exactly 11 characters
                    url = f"https://www.youtube.com/watch?v={v_id}"
                else:
                    console.print("[danger]Could not extract a valid video path from selection.[/danger]")
                    continue

            #load info natively from metadata function
            res = load_and_route(url, base_path)
            if res == "quit": return "quit"

def playlist_flow(url: str, base_path: str, db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    """Natively extract a YouTube playlist via yt-dlp and download items cleanly."""
    v_dir, a_dir = get_subdirs(base_path)
    has_ffmpeg = check_ffmpeg()

    # 1. Fetch flat playlist metadata using yt-dlp dictionary mapping
    ydl_opts_meta = {'extract_flat': True, 'quiet': True, 'no_warnings': True}

    with console.status("[info]Loading playlist info via yt-dlp...[/info]"):
        try:
            with YoutubeDL(ydl_opts_meta) as ydl:
                pl_info = ydl.extract_info(url, download=False)
            pl_title = pl_info.get('title', 'Unknown Playlist')
            entries = pl_info.get('entries', []) or []
        except Exception as e:
            console.print(f"[danger]Failed to load playlist records: {e}[/danger]")
            return "back"

    console.print(Panel(
        f"[highlight]{pl_title}[/highlight]\n[muted]{len(entries)} videos found[/muted]", 
        title="Playlist Loaded"
    ))

    # 2. Reusing your logic instead of rewriting menus!
    console.print("\n[info]Select Playlist Download Mode:[/info]")
    console.print("  [bold yellow]1[/bold yellow]  Download as Video Files")
    console.print("  [bold yellow]2[/bold yellow]  Download as Audio Tracks")
    console.print("  [bold yellow]b[/bold yellow]  Back")
    
    mode = Prompt.ask("[info]Choice[/info]", choices=["1", "2", "b"], default="2")
    if mode == "b": return "back"

    # If they want video, ask for quality
    if mode == "1":
        console.print("\n[info]Video Quality Settings:[/info]")
        if has_ffmpeg:
            console.print("  [bold yellow]1[/bold yellow]  Best Quality Video (1080p/4K - Merges A/V)")
        console.print("  [bold yellow]2[/bold yellow]  Standard Video (720p max)")
        video_choice = Prompt.ask("[info]Choice[/info]", choices=["1", "2"], default="2")

    # If they want audio, RECALL your existing bulk formats function!
    else:
        # ◄ HERE IS THE RECALL! It pulls your entire audio menu instantly.
        audio_choice = get_bulk_format_choice()
        if not audio_choice:
            return "back"

    # 3. Download Loop
    for i, entry in enumerate(entries, 1):
        if not entry: continue
        v_title = entry.get('title', 'Unknown Title')
        video_id = entry.get('id')
        if not video_id: continue
            
        video_url = f"https://youtube.com/watch?v={video_id}"
        console.print(Rule(f"[dim]{i}/{len(entries)}: {v_title[:60]}[/dim]"))
        
        try:
            # Execute Video Rules
            if mode == "1":
                if video_choice == "1" and has_ffmpeg:
                    download_video_merged(video_url, base_path)
                else:
                    # Native standard 720p video dictionary config
                    ydl_opts = {
                        'format': 'bestvideo[height<=720]+bestaudio/best',
                        'merge_output_format': 'mp4',
                        'outtmpl': str(v_dir / '%(title)s.%(ext)s'),
                        'quiet': True,
                    }
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([video_url])
            
            # Execute Audio Rules (Recalling your format downloader engine!)
            else:
                download_audio_format(
                    video_url=video_url, 
                    out_dir=base_path, 
                    format_choice=audio_choice,
                    db=db
                )
                
        except Exception as e:
            console.print(f"[danger]Skipped track: {e}[/danger]")

    console.print("[success]✔ Playlist bulk processing complete![/success]")
    return "back"
def load_and_route(url: str, base_path: str, db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    is_list = "list=" in url
    try:
        if is_list:
            # Pass parameters forward into playlist pipeline
            return playlist_flow(url, base_path, db=db, failed_log=failed_log)
        else:
            # ◄ NATIVE FIX: Fetch the raw information dictionary directly using yt-dlp
            # Fix: Ensure cookies are loaded during metadata extraction to prevent throttling/errors
            ydl_opts = {'quiet': True, 'no_warnings': True, 'extractor_args': {'youtube': {'player_client': ['default', '-android_sdkless']}},}
            cookie_path = Path.cwd() / ".youtube_cookies.txt"
            if cookie_path.exists() and cookie_path.stat().st_size > 0:
                ydl_opts['cookiefile'] = str(cookie_path)

            with console.status("[info]Loading native metadata via yt-dlp...[/info]"):
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            
            # FIX: Pass the database and failed logging handlers downstream into your video actions engine
            return video_menu(info_dict=info, base_path=base_path, db=db, failed_log=failed_log)
    except Exception as e:
        console.print(f"[danger]Error loading URL: {e}[/danger]")
        return "back"

def direct_url_flow(base_path: str, db: LibraryDB = None, failed_log: FailedLog = None) -> str:
    """Processes pasted video URLs and runs tracking variables through the pipeline"""
    while True:
        console.print(Rule(style="cyan"))
        url = Prompt.ask("[info]Paste URL[/info] (or 'b' back)", default="b").strip()
        if url.lower() == "b": return "back"
        if url.lower() == "q": return "quit"

        # This ensures the pipeline doesn't drop the tracking hooks
        res = load_and_route(url, base_path, db=db, failed_log=failed_log)
        if res == "quit": return "quit"



#save 4 later
def embed_spotify_metadata(file_path: str | Path, spotify_track_data: dict) -> bool:
    """
    Opens an MP3 file and embeds Spotify metadata directly into its tags.
    """
    path = Path(file_path).resolve()
    
    # Safety check: make sure the file actually exists and is an MP3
    if not path.exists() or path.suffix.lower() != '.mp3':
        console.print(f"[warning]Cannot embed metadata: {path.name} is missing or not an MP3[/warning]")
        return False
        
    try:
        # Load the MP3 file's ID3 tags (or create them if they don't exist)
        try:
            audio = EasyID3(str(path))
        except ID3NoHeaderError:
            # If the file has absolutely no metadata header yet, initialize it
            audio = EasyID3()
            audio.filename = str(path)
            
        # Inject the Spotify data cleanly
        audio['title'] = spotify_track_data.get('name', 'Unknown Title')
        audio['artist'] = spotify_track_data.get('artist', 'Unknown Artist')
        audio['album'] = spotify_track_data.get('album', 'Unknown Album')
        
        # Save the changes directly to the file
        audio.save()
        console.print(f"[success]▶ Metadata embedded into:[/success] {path.name}")
        return True
        
    except Exception as e:
        console.print(f"[danger]Failed to embed metadata: {e}[/danger]")
        return False





def main() -> None:
    show_banner()
    
    # 1. Verify system tool states natively via Nix profile environments
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

    # 2. Maintain your core modular tracking pipeline definitions
    cfg = load_config()
    base_path = get_output_path(cfg)
    spotify_mgr = SpotifyManager(cfg)
    db = LibraryDB()
    failed_log = FailedLog()
    COOKIE_FILE = sync_browser_cookies("brave")

    with YoutubeDL({'quiet': True}) as ydl:
        ydl.cache.remove()

    # 3. Ultimate Interactive Dashboard Loop
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

        # ◄ SAFE CHECK: Fixed evaluation mapping to prevent TypeErrors
        if failed_log.count() > 0:
            console.print(f"  [bold yellow]6[/bold yellow]  ⟳  Retry Failed Downloads [warning]({failed_log.count()} pending)[/warning]")

        console.print("  [bold yellow]q[/bold yellow]  Quit")

        choice = Prompt.ask("[info]Main Menu[/info]", default="1").lower()

        if choice == "q":
            break
        elif choice == "1":
            # Routes straight into your clean, native yt-dlp search engine
            search_flow(base_path, db=db, failed_log=failed_log)
        elif choice == "2":
            # Routes straight into your raw URL processing engine
            direct_url_flow(base_path, db=db, failed_log=failed_log)
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

    # 4. Explicit declarative storage connection safe closure
    db.close()
    console.print("\n[muted]Goodbye.[/muted]")


# =====================================================================
# 🚀 CORE SCRIPT EXECUTION BOOT INTERCEPTOR
# =====================================================================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[warning]⚠ Operation cancelled by user. Exiting downloader cleanly...[/warning]\n")
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
