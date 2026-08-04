#!/usr/bin/env python3
"""🎨 RICH CONSOLE UI & MENU INTERFACE LAYER - Where Beauty Meets Function"""

from datetime import timedelta
from pathlib import Path
from typing import List, Dict, Optional


try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn, MofNCompleteColumn
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from rich.text import Text
    from rich.rule import Rule
    from rich.theme import Theme
    
    custom_theme = Theme({
        "info": "bold cyan",
        "warning": "bold magenta",
        "danger": "bold red",
        "success": "bold green",
        "title": "bold white on blue",
        "url": "underline cyan",
        "highlight": "bold yellow",
        "muted": "dim white",
    })
    
    console = Console(theme=custom_theme)
except ImportError:
    console = None


def show_banner() -> None:
    """Show the beautiful banner that welcomes you"""
    banner = Text()
    banner.append("  ▶  ", style="bold red")
    banner.append("YouTube Downloader Pro", style="bold white")
    banner.append("  + Spotify", style="bold green")
    banner.append("  ▶  ", style="bold red")
    
    if console:
        console.print(Panel(banner, style="bold blue", padding=(1, 4)))


def show_stats_screen(db) -> None:
    """Show your collection stats - the mistress's pride"""
    stats = db.get_stats()

    if console:
        console.print(Rule("[info]📊 Download Library Stats[/info]"))

        size_str = fmt_size(stats["total_size"]) if stats["total_size"] else "0 B"
        
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
            for title, artist, when in stats["recent"]:
                try:
                    dt = datetime.fromisoformat(when)
                    when_str = dt.strftime("%d %b %Y %H:%M")
                except Exception:
                    when_str = when[:16]
                t.add_row(title[:35], artist or "?", when_str)
            console.print(t)

        if console:
            Prompt.ask("\n[muted]Press Enter to go back[/muted]", default="")


def video_menu(url: str, base_path: str) -> str:
    """Choose how you want your download"""
    from rich.panel import Panel
    
    v_dir, a_dir = get_subdirs(base_path)
    
    try:
        info = get_video_info(url)
        
        console.print(Panel(
            f"[bold white]Title:[/]      {info['title']}\n"
            f"[bold white]Author:[/]     {info['uploader']}\n"
            f"[bold white]Duration:[/]   {fmt_duration(info['duration'])}\n"
            f"[bold white]Views:[/]      {fmt_views(info['view_count'])}",
            title="[title] Video Info [/title]", border_style="blue"
        ))
        
        console.print("[info]Actions:[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Best Quality Video (MP4)")
        console.print("  [bold yellow]2[/bold yellow]  Audio Only (.mp3/.m4a)")
        console.print("  [bold yellow]b[/bold yellow]  Back")
        
        choice = Prompt.ask("[info]Choice", default="b").strip().lower()
        
        if choice == "1":
            download_video(url, v_dir)
        elif choice == "2":
            download_audio(url, a_dir)
        
        return "back"
        
    except Exception as e:
        console.print(f"[danger]Error: {e}[/danger]")
        return "back"


def search_flow(base_path: str) -> str:
    """Search for your desired content"""
    while True:
        query = Prompt.ask("\n[info]Search[/info] (or 'b' back)", default="b").strip()
        if query.lower() == "b": return "back"
        
        console.print("[info]Searching...[/info]")
        results = search_youtube(query)
        
        if not results:
            console.print("[warning]No results found.[/warning]")
            continue
        
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("#", style="yellow", width=4)
        table.add_column("Title", style="white")
        table.add_column("Length", style="dim")
        
        for i, v in enumerate(results, 1):
            table.add_row(str(i), v['title'][:60], fmt_duration(v.get('duration', 0)))
        
        console.print(table)
        
        c = Prompt.ask("[info]Pick #[/info]", default="b")
        if c.lower() == "b": continue
        
        if c.isdigit() and 1 <= int(c) <= len(results):
            url = results[int(c) - 1]['url']
            res = video_menu(url, base_path)
            if res == "quit": return "quit"


def settings_menu(cfg: dict) -> str:
    """Configure your preferences"""
    from config_manager import save_config  # ← FIX! Import here
    
    curr = cfg.get("output_path")
    
    console.print(Rule("Settings"))
    console.print(f"Current Path: [url]{curr}[/url]")
    console.print("  [bold yellow]1[/bold yellow]  Change Path")
    console.print("  [bold yellow]2[/bold yellow]  Reset Spotify Credentials")
    console.print("  [bold yellow]3[/bold yellow]  Clear Spotify Cache (Fix Permission Issues)")
    console.print("  [bold yellow]b[/bold yellow]  Back")

    choice = Prompt.ask("Choice", default="b")
    
    if choice == "1":
        new_p = Prompt.ask("New Path")
        cfg["output_path"] = new_p
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
        return "back"


def clear_spotify_cache() -> None:
    """Clear Spotify cache - fresh start"""
    cache_locations = [
        Path.home() / ".cache",
        Path.home() / ".spotify_cache",
    ]
    
    deleted_count = 0
    
    for cache_dir in cache_locations:
        if cache_dir.exists() and cache_dir.is_dir():
            spotify_files = list(cache_dir.glob("*spotify*"))
            
            for cache_file in spotify_files:
                try:
                    if cache_file.is_file():
                        cache_file.unlink()
                        console.print(f"[success]✔ Deleted: {cache_file}[/success]")
                        deleted_count += 1
                except Exception as e:
                    console.print(f"[warning]Could not delete {cache_file}: {e}[/warning]")
    
    if deleted_count > 0:
        console.print(f"\n[success]✔ Cleared {deleted_count} cache file(s)![/success]")


def nav_hint(options: list[str]) -> None:
    """Navigation hints for you"""
    console.print(f"[nav]  ( {' · '.join(options)} )[/nav]")
