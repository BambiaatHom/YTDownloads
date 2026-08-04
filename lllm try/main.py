#!/usr/bin/env python3
"""👑 MAIN ENTRY POINT - Where Mistress Rules Over Your Downloads 👑"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# Import all modules - just like you belong to me
from config_manager import (
    load_config, save_config, get_output_path, 
    LibraryDB, FailedLog, check_ffmpeg, get_subdirs
)
from youtube_downloader import (
    search_youtube, download_audio, download_video,
    fmt_duration, fmt_views, fmt_size, is_valid_url
)
from spotify_manager import SpotifyManager, SPOTIFY_AVAILABLE

# Import console and Prompt from ui_interface! ← CRITICAL FIXES
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm  # ← BOTH NEEDED!
from rich.table import Table
from rich.rule import Rule
from ui_interface import (
    show_banner, video_menu, search_flow, settings_menu,
    show_stats_screen, clear_spotify_cache, Rule, console
)


def main() -> None:
    """The mistress's throne room - where all commands begin"""
    
    console = Console()
    
    show_banner()
    
    if not check_ffmpeg():
        console.print(Panel(
            "[warning]FFmpeg not found![/warning]\n"
            "You can still download videos up to 720p.\n"
            "To enable 1080p/4K and audio format conversion, please install FFmpeg.",
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

        choice = Prompt.ask("[info]Main Menu", default="1").lower()

        if choice == "q":
            break
        elif choice == "1":
            search_flow(base_path)
        elif choice == "2":
            url = Prompt.ask("[info]Paste URL", default="b")
            if url.lower() != "b": video_menu(url, base_path)
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


def spotify_import_flow(spotify_mgr, base_path, db=None, failed_log=None):
    """Bring your Spotify collection to me"""
    
    if not spotify_mgr.sp:
        if not spotify_mgr.setup_spotify():
            return "back"
    
    while True:
        console.print(Rule(style="green"))  # ← NOW WORKS!
        console.print("[info]Spotify Import Options:[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Import Liked Songs")
        console.print("  [bold yellow]2[/bold yellow]  Import from Playlist")
        console.print("  [bold yellow]3[/bold yellow]  Import Top Tracks")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        
        choice = Prompt.ask("[info]Choice", default="b").strip().lower()
        
        if choice == "b": return "back"
        elif choice == "1": import_liked_songs(spotify_mgr, base_path, db=db)
        elif choice == "2": import_playlist(spotify_mgr, base_path, db=db)
        elif choice == "3": import_top_tracks(spotify_mgr, base_path, db=db)


def import_liked_songs(spotify_mgr, base_path, db=None):
    """Download all your favorite songs - just for me"""
    
    if Confirm.ask("[info]Download ALL liked songs?[/info]", default=True):
        limit = 999999
    else:
        limit = int(Prompt.ask("[info]How many liked songs to import?[/info]", default="50"))

    songs = spotify_mgr.get_liked_songs(limit=limit)
    
    if not songs:
        console.print("[warning]No liked songs found![/warning]")
        return
    
    console.print(f"[success]Found {len(songs)} liked songs![/success]\n")
    
    if not Confirm.ask(f"\n[info]Download all {len(songs)} songs?[/info]", default=True):
        return
    
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    v_dir, a_dir = get_subdirs(base_path)
    
    successful = 0
    failed = 0
    for i, song in enumerate(songs):
        console.print(f"[dim]Downloading {i+1}/{len(songs)}: {song['search_query']}[/dim]")
        
        try:
            download_audio(song['search_query'], a_dir / "Liked Songs", format_choice)
            successful += 1
            
            # 🔥 ADD DELAY BETWEEN DOWNLOADS!
            if i < len(songs) - 1:
                import time
                time.sleep(5)  # Wait 5 seconds between downloads
                
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
            failed += 1
            if failed_log:
                failed_log.add(song['search_query'], str(e), 'spotify')
    
    console.print(f"\n[success]✔ Downloaded {successful}/{len(songs)} tracks to 'Liked Songs' folder[/success]")
    if failed > 0:
        console.print(f"[warning]{failed} tracks skipped[/warning]")


def import_playlist(spotify_mgr, base_path, db=None):
    """Choose a playlist and let me download it all"""
    
    playlists = spotify_mgr.get_playlists()
    
    if not playlists:
        console.print("[warning]No playlists found![/warning]")
        return
    
    table = Table(box=box.SIMPLE)
    table.add_column("#", style="yellow")
    table.add_column("Name", style="white")
    table.add_column("Tracks", style="dim")
    
    for i, pl in enumerate(playlists, 1):
        table.add_row(str(i), pl['name'], str(pl['track_count']))
    
    console.print(table)
    
    choice = Prompt.ask("[info]Select playlist #[/info]", default="b")
    if choice.lower() == "b": return
    
    selected_playlist = playlists[int(choice) - 1]
    songs = spotify_mgr.get_playlist_tracks(selected_playlist['id'])
    
    console.print(f"[success]Found {len(songs)} tracks in '{selected_playlist['name']}'[/success]\n")
    
    if not Confirm.ask("[info]Download all tracks?[/info]", default=True):
        return
    
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    v_dir, a_dir = get_subdirs(base_path)
    safe_playlist = "".join([c for c in selected_playlist['name'] if c.isalnum() or c in (' ', '-', '_')]).strip()
    
    successful = 0
    failed = 0
    for song in songs:
        try:
            download_audio(song['search_query'], a_dir / safe_playlist, format_choice)
            successful += 1
            
            if db:
                db.add(
                    title=song['name'],
                    artist=song['artist'],
                    album=song.get('album', ''),
                    youtube_url='',
                    file_path=str(a_dir / safe_playlist),
                    fmt=format_choice,
                    file_size=0,
                    source='spotify'
                )
                
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
            failed += 1
            if failed_log:
                failed_log.add(song['search_query'], str(e), 'playlist')
    
    console.print(f"[success]✔ Downloaded {successful}/{len(songs)} tracks[/success]")
    if failed > 0:
        console.print(f"[warning]{failed} tracks skipped[/warning]")


def import_top_tracks(spotify_mgr, base_path, db=None):
    """Your top tracks - now in your collection"""
    
    if Confirm.ask("[info]Download ALL top tracks?[/info]", default=True):
        limit = 50
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
    
    v_dir, a_dir = get_subdirs(base_path)
    top_folder = a_dir / "Top Tracks"
    top_folder.mkdir(parents=True, exist_ok=True)
    
    successful = 0
    failed = 0
    for song in songs:
        try:
            download_audio(song['search_query'], top_folder, format_choice)
            successful += 1
            
            if db:
                db.add(
                    title=song['name'],
                    artist=song['artist'],
                    album='',
                    youtube_url='',
                    file_path=str(top_folder),
                    fmt=format_choice,
                    file_size=0,
                    source='spotify'
                )
                
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
            failed += 1
            if failed_log:
                failed_log.add(song['search_query'], str(e), 'top_tracks')
    
    console.print(f"[success]✔ Downloaded {successful}/{len(songs)} tracks to 'Top Tracks' folder[/success]")
    if failed > 0:
        console.print(f"[warning]{failed} tracks skipped[/warning]")


def get_bulk_format_choice() -> str:
    """Choose your preferred audio format"""
    
    console.print("\n[info]Choose download format for all tracks:[/info]")
    console.print("  [bold yellow]1[/bold yellow]  MP3 320kbps")
    console.print("  [bold yellow]2[/bold yellow]  MP3 192kbps")
    console.print("  [bold yellow]3[/bold yellow]  WAV")
    console.print("  [bold yellow]4[/bold yellow]  FLAC")
    console.print("  [bold yellow]5[/bold yellow]  OGG")
    console.print("  [bold yellow]6[/bold yellow]  M4A")
    
    choice = Prompt.ask("[info]Format", default="1")
    return choice if choice in ["1", "2", "3", "4", "5", "6"] else None


def retry_failed_downloads(failed_log, base_path, db):
    """Give failed downloads another chance"""
    
    entries = failed_log.entries
    if not entries:
        console.print("[info]No failed downloads to retry![/info]")
        return

    console.print(f"[info]Found {len(entries)} failed downloads[/info]\n")
    
    if not Confirm.ask("[info]Retry all?[/info]", default=True):
        return
    
    format_choice = get_bulk_format_choice()
    if not format_choice:
        return
    
    songs = [{"name": e["query"], "search_query": e["query"]} for e in entries]
    
    failed_log.clear()
    
    v_dir, a_dir = get_subdirs(base_path)
    retry_folder = a_dir / "Retried Downloads"
    retry_folder.mkdir(parents=True, exist_ok=True)
    
    successful = 0
    failed = 0
    for song in songs:
        try:
            download_audio(song['search_query'], retry_folder, format_choice)
            successful += 1
            
            if db:
                db.add(
                    title=song['name'],
                    artist='',
                    album='',
                    youtube_url='',
                    file_path=str(retry_folder),
                    fmt=format_choice,
                    file_size=0,
                    source='retry'
                )
                
        except Exception as e:
            console.print(f"[danger]Skipped: {e}[/danger]")
            failed += 1
    
    console.print(f"\n[success]✔ Downloaded {successful}/{len(songs)} tracks to 'Retried Downloads' folder[/success]")
    if failed > 0:
        console.print(f"[warning]{failed} tracks skipped[/warning]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
