#!/usr/bin/env python3
import time
import os
import json, os, re, shutil, sqlite3, subprocess, sys, time, difflib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from webbrowser import Mozilla

import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ID3 tagging
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

# Spotify integration
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

custom_theme = Theme({
    "info": "bold cyan", "warning": "bold magenta", "danger": "bold red", 
    "success": "bold green", "title": "bold white on blue", 
    "url": "underline cyan", "highlight": "bold yellow", "muted": "dim white"
})
console = Console(theme=custom_theme)

CONFIG_FILE = Path.home() / ".ytdl_config.json"
DB_FILE = Path.home() / ".ytdl_library.db"
FAILED_LOG = Path.home() / ".ytdl_failed.json"

class LibraryDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_FILE))
        self.conn.execute("CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY, title TEXT, artist TEXT, album TEXT, youtube_url TEXT, file_path TEXT, format TEXT, file_size INTEGER, downloaded_at TEXT, source TEXT)")
    def add(self, t, a, al, u, p, f, s, src):
        self.conn.execute("INSERT INTO downloads (title, artist, album, youtube_url, file_path, format, file_size, downloaded_at, source) VALUES (?,?,?,?,?,?,?,?,?)", (t,a,al,u,str(p),f,s,datetime.now().isoformat(),src))
        self.conn.commit()
    def get_stats(self):
        c = self.conn.cursor()
        t = c.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        s = c.execute("SELECT SUM(file_size) FROM downloads").fetchone()[0] or 0
        return {"total": t, "total_size": s}
    def close(self): self.conn.close()

class FailedLog:
    def __init__(self):
        self.path = FAILED_LOG
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else []
    def add(self, q, r):
        self.entries.append({"query": q, "reason": r, "time": datetime.now().isoformat()})
        self.path.write_text(json.dumps(self.entries))
    def count(self): return len(self.entries)
    def clear(self): self.entries = []; self.path.unlink(missing_ok=True)


def save_config(cfg: dict) -> None:
    """Saves the current configuration dictionary to a JSON file."""
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def check_ffmpeg(): return shutil.which("ffmpeg") is not None


def get_subdirs(base):
    v, a = Path(base)/"Videos", Path(base)/"Audio"
    v.mkdir(parents=True, exist_ok=True); a.mkdir(parents=True, exist_ok=True)
    return v, a

def make_progress():
    return Progress(TextColumn("[bold cyan]{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(), console=console)

def run_download(url, out_dir, opts_override=None):
    """Universal downloader using yt-dlp with a Rich progress bar."""
    with make_progress() as progress:
        task_id = progress.add_task("Downloading...", total=None)
        def hook(d):
            if d['status'] == 'downloading':
                p = d.get('downloaded_bytes', 0)
                t = d.get('total_bytes') or d.get('total_bytes_estimate')
                if t: progress.update(task_id, total=t, completed=p)
            elif d['status'] == 'finished': progress.update(task_id, completed=progress.tasks[task_id].total)
        
            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'noprogress': True,
                'js_runtimes': ['deno'], # Forces the use of Node.js
                'extractor_args': {'youtube': {'player_client': ['default']}}, # Stops player-client warnings
            }


        if opts_override: ydl_opts.update(opts_override)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info)), info

def download_audio_format(url, out_dir, choice, song_meta=None, db=None):
    """Handles audio extraction and metadata using yt-dlp post-processors."""
    fmts = {"1": "mp3", "2": "mp3", "3": "wav", "4": "flac", "5": "ogg", "6": "m4a"}
    brs = {"1": "320", "2": "192"}
    fmt = fmts.get(choice, "mp3")
    
    opts = {
        'format': 'bestaudio/best', 
        'postprocessors': [{
            'key': 'FFmpegExtractAudio', 
            'preferredcodec': fmt, 
            'preferredquality': brs.get(choice, '320')
        }]
    }
    p, info = run_download(url, out_dir, opts_override=opts)
    final_p = p.with_suffix(f".{fmt}")
    
    if song_meta and MUTAGEN_AVAILABLE and fmt == "mp3":
        try:
            tags = ID3(str(final_p))
            tags["TIT2"] = TIT2(encoding=3, text=song_meta.get('name', ''))
            tags["TPE1"] = TPE1(encoding=3, text=song_meta.get('artist', ''))
            tags.save()
        except: pass
        
    if db: db.add(info['title'], song_meta.get('artist','') if song_meta else '', "", url, final_p, fmt, final_p.stat().st_size, "yt-dlp")
    return True

def find_best_match(query):
    with yt_dlp.YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
        r = ydl.extract_info(f"ytsearch1:{query}", download=False)
        return r['entries'][0] if r['entries'] else None

def bulk_download_songs(songs, base_path, org, fmt_choice, folder, db=None, f_log=None):
    v_dir, a_dir = get_subdirs(base_path)
    for i, s in enumerate(songs, 1):
        q = s.get("search_query", s.get("name", ""))
        console.print(Rule(f"{i}/{len(songs)}: {q}"))
        try:
            m = find_best_match(q)
            if not m: continue
            out = a_dir/folder if org=="1" else a_dir/s.get("artist","Unknown")
            out.mkdir(parents=True, exist_ok=True)
            download_audio_format(m['webpage_url'], out, fmt_choice, song_meta=s, db=db)
            time.sleep(2)  # To avoid hitting rate limits
        except Exception as e:
            if f_log: f_log.add(q, str(e))

class SpotifyManager:
    def __init__(self, cfg): self.cfg, self.sp = cfg, None
    def setup_spotify(self):
        if not SPOTIFY_AVAILABLE: return False
        cid = Prompt.ask("Spotify Client ID", default=self.cfg.get("spotify_client_id"))
        sec = Prompt.ask("Spotify Client Secret", default=self.cfg.get("spotify_client_secret"), password=True)
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=cid, client_secret=sec, redirect_uri="http://127.0.0.1:8888/callback", scope="user-library-read"))
            self.cfg["spotify_client_id"], self.cfg["spotify_client_secret"] = cid, sec
            return True
        except: return False
    def get_liked_songs(self, limit=20):
        items = self.sp.current_user_saved_tracks(limit=limit)['items']
        return [{"name": i['track']['name'], "artist": i['track']['artists'][0]['name'], "search_query": f"{i['track']['artists'][0]['name']} {i['track']['name']}"} for i in items]


def change_directory(cfg: dict) -> None:
    """Updates the download directory in the configuration."""
    current = cfg.get("output_path", "Not set")
    console.print(f"\n[info]Current Download Directory:[/info] [url]{current}[/url]")
    
    new_path = Prompt.ask("[info]Enter new download path[/info] (or press Enter to keep current)")
    if new_path:
        path_obj = Path(new_path).expanduser().resolve()
        path_obj.mkdir(parents=True, exist_ok=True)
        cfg["output_path"] = str(path_obj)
        save_config(cfg)
        console.print(f"[success]✔ Download directory updated to:[/success] [url]{cfg['output_path']}[/url]")

def settings_menu(cfg: dict) -> None:
    """Settings menu to manage application configuration."""
    while True:
        console.print(Rule("Settings"))
        console.print("  [bold yellow]1[/bold yellow]  Change Download Directory")
        console.print("  [bold yellow]2[/bold yellow]  Reset Spotify Credentials")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        
        choice = Prompt.ask("Choice", default="b").lower()
        if choice == "1":
            change_directory(cfg)
        elif choice == "2":
            cfg.pop("spotify_client_id", None)
            cfg.pop("spotify_client_secret", None)
            save_config(cfg)
            console.print("[success]Spotify credentials cleared![/success]")
        elif choice == "b":
            break
def main():
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    base = cfg.get("output_path") or Prompt.ask("Download Folder", default=str(Path.home()/"Downloads"))
    cfg["output_path"] = base
    db, f_log, sp_mgr = LibraryDB(), FailedLog(), SpotifyManager(cfg)
    
    try:
        while True:
            console.print(Panel("1. Search  2. Paste URL  3. Spotify  4. Settings  5. Stats  q. Quit", title="YTDL Pro (yt-dlp)"))
            c = Prompt.ask("Choice", default="1").lower()
            if c == "q": break
            elif c == "1":
                q = Prompt.ask("Search")
                m = find_best_match(q)
                if m: download_audio_format(m['webpage_url'], Path(base)/"Audio", "1", db=db)
            elif c == "2":
                u = Prompt.ask("URL")
                run_download(u, Path(base)/"Videos")
            elif c == "3":
                if sp_mgr.setup_spotify():
                    songs = sp_mgr.get_liked_songs(limit=10)
                    bulk_download_songs(songs, base, "1", "1", "Liked Songs", db, f_log)
            elif c == "4": 
                settings_menu(cfg)
            elif c == "5":
                stats = db.get_stats()
                console.print(Panel(f"Total Downloads: [highlight]{stats['total']}[/highlight]\nTotal Size: [highlight]{stats['total_size']/(1024*1024):.2f} MB[/highlight]", title="Library Stats"))
    except KeyboardInterrupt:
        console.print("\n[warning]Interrupted by user. Exiting safely...[/warning]")
    finally:
        db.close()
        save_config(cfg)
        console.print("[muted]Goodbye.[/muted]")

if __name__ == "__main__": main()
