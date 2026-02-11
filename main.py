"""
YouTube Downloader
──────────────────
Navigation model
  main_menu()
    ├─ search_flow()        → search → results → video_menu()
    └─ direct_url_flow()    → enter URL → video_menu() / playlist_flow()

  video_menu(yt)            ← always returns here after a download
    ├─ download (video / audio / stream / captions)
    ├─ [b] back             → back to wherever called from
    └─ [q] quit

Every function returns a string "back" | "quit" | "ok"
so the caller can bubble the signal up the chain.
"""

import json
from pathlib import Path

from pytubefix import YouTube, Search, Playlist
from pytubefix.exceptions import VideoUnavailable, RegexMatchError
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress, BarColumn, DownloadColumn,
    TransferSpeedColumn, TimeRemainingColumn,
)
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ── Theme ────────────────────────────────────────────────────────────────────
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


# ── Config (persisted output path) ───────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_output_path(cfg: dict) -> str:
    """Return saved path or ask the user and save it."""
    if "output_path" in cfg:
        return cfg["output_path"]

    console.print(Panel(
        "[info]No download folder set yet.[/info]\n"
        "Enter the base folder where downloads will be saved.\n"
        "[muted]Videos → <folder>/Videos/   Audio → <folder>/Audio/[/muted]",
        border_style="cyan",
        padding=(1, 2),
    ))
    path = Prompt.ask("[info]Download folder[/info]", default=str(Path.home() / "Downloads" / "YTDownloads"))
    cfg["output_path"] = path
    save_config(cfg)
    console.print(f"[success]✔  Saved to config:[/success] [url]{path}[/url]\n")
    return path


def video_dir(base: str) -> str:
    p = Path(base) / "Videos"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def audio_dir(base: str) -> str:
    p = Path(base) / "Audio"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_duration(seconds) -> str:
    h, r = divmod(int(seconds or 0), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def fmt_views(n) -> str:
    if n is None:
        return "N/A"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_size(b) -> str:
    if b is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def make_progress() -> Progress:
    return Progress(
        "[bold cyan]{task.description}",
        BarColumn(bar_width=40, style="cyan", complete_style="green"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def nav_hint(options: list[str]) -> None:
    """Print a small navigation hint line."""
    console.print(f"[nav]  ( {' · '.join(options)} )[/nav]")


# ── Banner ───────────────────────────────────────────────────────────────────

def show_banner() -> None:
    banner = Text()
    banner.append("  ▶  ", style="bold red")
    banner.append("YouTube Downloader", style="bold white")
    banner.append("  ▶  ", style="bold red")
    console.print(Panel(banner, style="bold blue", padding=(1, 4)))


# ── Video metadata ────────────────────────────────────────────────────────────

def show_metadata(yt: YouTube) -> None:
    desc = (yt.description or "—").split("\n")[0][:120]
    if len(yt.description or "") > 120:
        desc += "…"
    keywords = ", ".join((yt.keywords or [])[:8]) or "N/A"
    publish = str(yt.publish_date.date()) if yt.publish_date else "N/A"

    info = (
        f"[bold white]Title:[/]      [highlight]{yt.title}[/]\n"
        f"[bold white]Author:[/]     [info]{yt.author}[/]\n"
        f"[bold white]Duration:[/]   {fmt_duration(yt.length)}\n"
        f"[bold white]Published:[/]  {publish}\n"
        f"[bold white]Views:[/]      {fmt_views(yt.views)}\n"
        f"[bold white]Thumbnail:[/]  [url]{yt.thumbnail_url}[/]\n"
        f"[bold white]Keywords:[/]   [muted]{keywords}[/]\n"
        f"[bold white]Desc:[/]       [muted]{desc}[/]"
    )
    console.print(Panel(
        info,
        title="[title] Video Info [/title]",
        border_style="blue",
        padding=(1, 2),
    ))


def show_chapters(yt: YouTube) -> None:
    chapters = getattr(yt, "chapters", None) or []
    if not chapters:
        return
    table = Table(
        box=box.SIMPLE_HEAD, border_style="blue",
        header_style="bold cyan", title="[chapter]Chapters[/chapter]",
    )
    table.add_column("#", style="bold yellow", justify="center", width=4)
    table.add_column("Timestamp", style="info", justify="center", width=12)
    table.add_column("Title", style="bold white")
    for i, ch in enumerate(chapters, start=1):
        table.add_row(str(i), fmt_duration(int(ch.start_seconds)), ch.title)
    console.print(table)


# ── Captions ──────────────────────────────────────────────────────────────────

def handle_captions(yt: YouTube, base_path: str) -> str:
    captions = yt.captions
    if not captions:
        console.print("[muted]No caption tracks available.[/muted]")
        return "ok"

    table = Table(
        box=box.SIMPLE_HEAD, border_style="magenta",
        header_style="bold magenta",
        title="[bold magenta]Available Caption Tracks[/bold magenta]",
    )
    table.add_column("Code", style="bold yellow", width=12)
    table.add_column("Language", style="bold white")
    for code, cap in captions.items():
        table.add_row(code, cap.name)
    console.print(table)

    nav_hint(["b = back"])
    code = Prompt.ask("[info]Enter caption code to save, or [b] to go back[/info]")
    if code.strip().lower() == "b":
        return "back"
    if code in captions:
        safe_title = yt.title[:50].replace("/", "_").replace("\\", "_")
        out_file = str(Path(base_path) / f"{safe_title}.srt")
        captions[code].save_captions(out_file)
        console.print(f"[success]✔  Saved → {out_file}[/success]")
    else:
        console.print("[danger]Caption code not found.[/danger]")
    return "ok"


# ── Stream picker ─────────────────────────────────────────────────────────────

def pick_stream(yt: YouTube):
    """Show all streams; return chosen stream or None to signal 'back'."""
    progressive = list(yt.streams.filter(progressive=True).order_by("resolution").desc())
    video_only = list(yt.streams.filter(only_video=True, file_extension="mp4").order_by("resolution").desc())
    audio_only = list(yt.streams.filter(only_audio=True).order_by("abr").desc())
    all_streams = progressive + video_only + audio_only

    table = Table(
        box=box.ROUNDED, border_style="cyan",
        header_style="bold cyan", show_lines=True,
        title="[bold white]Available Streams[/bold white]",
    )
    table.add_column("#", style="bold yellow", justify="center", width=4)
    table.add_column("Type", style="bold white", width=13)
    table.add_column("Res / ABR", style="info", justify="center", width=10)
    table.add_column("Format", style="highlight", justify="center", width=8)
    table.add_column("FPS", style="muted", justify="center", width=6)
    table.add_column("Size", style="success", justify="right", width=10)
    table.add_column("Progressive", style="muted", justify="center", width=12)

    for i, s in enumerate(all_streams, start=1):
        has_v = s.includes_video_track
        has_a = s.includes_audio_track
        kind = ("Audio only" if (has_a and not has_v) else
                "Video only" if (has_v and not has_a) else
                "Video + Audio")
        res = s.resolution or s.abr or "N/A"
        fps = str(s.fps) if getattr(s, "fps", None) else "—"
        prog = "[green]✔[/]" if s.is_progressive else "[red]✘[/]"
        table.add_row(str(i), kind, res, s.subtype, fps, fmt_size(s.filesize), prog)

    console.print(table)
    nav_hint(["number = pick stream", "b = back"])

    choice = Prompt.ask("[info]Stream number, or [b] to go back[/info]", default="b")
    if choice.strip().lower() == "b":
        return None
    if choice.strip().isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(all_streams):
            return all_streams[idx]
    console.print("[warning]Invalid — falling back to highest resolution.[/warning]")
    return yt.streams.get_highest_resolution()


# ── Core download helper ──────────────────────────────────────────────────────

def run_download(yt: YouTube, stream, out_dir: str) -> None:
    """Execute the download with a Rich progress bar."""
    console.print(Panel(
        f"[highlight]{yt.title}[/highlight]\n"
        f"[muted]Res/ABR:[/muted] {stream.resolution or stream.abr}  "
        f"[muted]Format:[/muted] {stream.subtype}  "
        f"[muted]Size:[/muted] {fmt_size(stream.filesize)}\n"
        f"[muted]Saving to:[/muted] [url]{out_dir}[/url]",
        title="[success] Starting Download [/success]",
        border_style="green", padding=(0, 2),
    ))

    total = stream.filesize or 0
    with make_progress() as progress:
        task = progress.add_task(yt.title[:50], total=total)

        def on_progress(s, chunk, bytes_remaining):
            progress.update(task, completed=s.filesize - bytes_remaining)

        yt.register_on_progress_callback(on_progress)
        stream.download(output_path=out_dir)

    console.print(Rule(style="green"))
    console.print(f"[success]✔  Saved to:[/success] [url]{out_dir}[/url]\n")


# ── Video action menu  ────────────────────────────────────────────────────────

def video_menu(yt: YouTube, base_path: str) -> str:
    """
    Show actions for a loaded video. Loops until the user picks back/quit.
    Returns "back" or "quit".
    """
    while True:
        console.print(Rule(style="cyan"))
        show_metadata(yt)
        show_chapters(yt)

        console.print(Rule(style="cyan"))
        console.print("[info]What would you like to do?[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Download — highest resolution video")
        console.print("  [bold yellow]2[/bold yellow]  Download — audio only (.m4a)")
        console.print("  [bold yellow]3[/bold yellow]  Download — choose a specific stream")
        console.print("  [bold yellow]4[/bold yellow]  Captions / subtitles")
        console.print("  [bold yellow]b[/bold yellow]  ← Back")
        console.print("  [bold yellow]q[/bold yellow]  Quit")
        nav_hint(["1-4 = action", "b = back", "q = quit"])

        mode = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()

        if mode == "q":
            return "quit"
        if mode == "b":
            return "back"

        if mode == "1":
            stream = yt.streams.get_highest_resolution()
            out_dir = video_dir(base_path)
            run_download(yt, stream, out_dir)

        elif mode == "2":
            stream = yt.streams.get_audio_only()
            out_dir = audio_dir(base_path)
            run_download(yt, stream, out_dir)

        elif mode == "3":
            stream = pick_stream(yt)
            if stream is None:
                continue  # user pressed b inside stream picker → back to this menu
            # Route to correct subfolder based on stream type
            if stream.includes_audio_track and not stream.includes_video_track:
                out_dir = audio_dir(base_path)
            else:
                out_dir = video_dir(base_path)
            run_download(yt, stream, out_dir)

        elif mode == "4":
            result = handle_captions(yt, base_path)
            if result == "back":
                continue
        else:
            console.print("[warning]Please choose a valid option.[/warning]")
            continue

        # After any completed action, prompt what to do next
        console.print(Rule(style="cyan"))
        console.print("[info]What next?[/info]")
        console.print("  [bold yellow]1[/bold yellow]  Do something else with this video")
        console.print("  [bold yellow]b[/bold yellow]  ← Back to previous screen")
        console.print("  [bold yellow]q[/bold yellow]  Quit")
        nav_hint(["1 = stay here", "b = back", "q = quit"])

        nxt = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()
        if nxt == "q":
            return "quit"
        if nxt == "b":
            return "back"
        # "1" → loop back to top of video_menu


# ── Search results table ─────────────────────────────────────────────────────

def show_results(videos) -> None:
    table = Table(
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
        show_lines=True,
        title="[bold white]Search Results[/bold white]",
        expand=True,
    )
    table.add_column("#", style="bold yellow", justify="center", width=4, no_wrap=True)
    table.add_column("Title", style="bold white", ratio=3, min_width=30)
    table.add_column("Duration", style="info", justify="center", width=12, no_wrap=True)
    table.add_column("URL", style="url", ratio=2, min_width=36, overflow="fold")

    for i, video in enumerate(videos, start=1):
        duration = fmt_duration(video.length) if video.length else "—"
        table.add_row(str(i), video.title or "Untitled", duration, video.watch_url)

    console.print(table)


# ── Search flow ───────────────────────────────────────────────────────────────

def search_flow(base_path: str) -> str:
    """
    Search → results table → pick URL → video_menu.
    Loops so you can search again after going back from a video.
    Returns "quit" or "back".
    """
    while True:
        console.print(Rule(style="cyan"))
        nav_hint(["b = back to main menu", "q = quit"])
        query = Prompt.ask("\n[info]Search[/info]", default="b").strip()

        if query.lower() == "q":
            return "quit"
        if query.lower() == "b":
            return "back"

        console.print(f"\n[info]Searching for:[/info] [highlight]{query}[/highlight]\n")
        with console.status("[info]Fetching results…[/info]", spinner="dots"):
            results = Search(query)

        if not results.videos:
            console.print("[danger]No results found. Try a different search.[/danger]")
            continue

        # Print the table ONCE per search
        show_results(results.videos)
        videos = results.videos

        # ── Results loop — pick by number, table stays visible above ─────────
        while True:
            console.print(Rule(style="cyan"))
            nav_hint([f"1–{len(videos)} = pick video", "b = new search", "q = quit"])
            choice = Prompt.ask("[info]Pick a number[/info]", default="b").strip().lower()

            if choice == "q":
                return "quit"
            if choice == "b":
                break  # back to search prompt

            if not choice.isdigit() or not (1 <= int(choice) <= len(videos)):
                console.print(f"[warning]Enter a number between 1 and {len(videos)}, or b / q.[/warning]")
                continue

            url = videos[int(choice) - 1].watch_url
            signal = _load_and_show(url, base_path)
            if signal == "quit":
                return "quit"
            # "back" → loop back so user can pick another video from the same results
            # Reprint the table so they can see their options again
            show_results(videos)


def _load_and_show(url: str, base_path: str) -> str:
    """Load a URL (auto-detect playlist vs video) and enter the right menu."""
    is_playlist = "playlist" in url.lower() or "list=" in url.lower()
    try:
        if is_playlist:
            return playlist_flow(url, base_path)
        else:
            with console.status("[info]Fetching video info…[/info]", spinner="dots"):
                yt = YouTube(url, use_oauth=True, allow_oauth_cache=True)
            return video_menu(yt, base_path)
    except VideoUnavailable:
        console.print("[danger]✘  Video unavailable (private, deleted, or region-locked).[/danger]")
        return "back"
    except RegexMatchError:
        console.print("[danger]✘  That doesn't look like a valid YouTube URL.[/danger]")
        return "back"
    except Exception as exc:
        console.print(f"[danger]✘  Error:[/danger] {exc}")
        return "back"


# ── Direct URL flow ───────────────────────────────────────────────────────────

def direct_url_flow(base_path: str) -> str:
    """Ask for a URL directly, loop until back/quit."""
    while True:
        console.print(Rule(style="cyan"))
        nav_hint(["paste a video or playlist URL", "b = back", "q = quit"])
        url = Prompt.ask("[info]URL[/info]", default="b").strip()

        if url.lower() == "q":
            return "quit"
        if url.lower() == "b":
            return "back"

        signal = _load_and_show(url, base_path)
        if signal == "quit":
            return "quit"
        # "back" → loops back to URL prompt


# ── Playlist flow ─────────────────────────────────────────────────────────────

def playlist_flow(url: str, base_path: str) -> str:
    with console.status("[info]Loading playlist…[/info]", spinner="dots"):
        pl = Playlist(url)

    total_videos = len(pl.video_urls)
    console.print(Panel(
        f"[highlight]{pl.title}[/highlight]\n[muted]{total_videos} videos[/muted]",
        title="[title] Playlist [/title]",
        border_style="blue",
    ))

    console.print("[info]Download type for all videos?[/info]")
    console.print("  [bold yellow]1[/bold yellow]  Video (highest resolution)  → Videos/")
    console.print("  [bold yellow]2[/bold yellow]  Audio only                  → Audio/")
    console.print("  [bold yellow]b[/bold yellow]  ← Back")
    nav_hint(["1 = video", "2 = audio", "b = back"])

    mode = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()
    if mode == "b":
        return "back"

    out_dir = video_dir(base_path) if mode == "1" else audio_dir(base_path)

    if not Confirm.ask(f"[info]Download all {total_videos} videos to [url]{out_dir}[/url]?[/info]", default=True):
        return "back"

    for i, video in enumerate(pl.videos, start=1):
        console.print(Rule(f"[cyan]{i}/{total_videos}[/cyan]  [white]{video.title[:60]}[/white]"))
        try:
            stream = video.streams.get_highest_resolution() if mode == "1" else video.streams.get_audio_only()
            total = stream.filesize or 0

            with make_progress() as progress:
                task = progress.add_task(video.title[:45], total=total)

                def _cb(s, chunk, remaining, _t=task, _p=progress):
                    _p.update(_t, completed=s.filesize - remaining)

                video.register_on_progress_callback(_cb)
                stream.download(output_path=out_dir)

            console.print("[success]✔  Done[/success]")
        except Exception as exc:
            console.print(f"[danger]✘  Skipping — {exc}[/danger]")

    console.print(Rule(style="green"))
    console.print(f"[success]Playlist download complete![/success]  Saved to [url]{out_dir}[/url]\n")

    nxt = Prompt.ask(
        "[info]Back to menu (b) or quit (q)?[/info]",
        choices=["b", "q"], default="b",
    ).strip().lower()
    return "quit" if nxt == "q" else "back"


# ── Settings ──────────────────────────────────────────────────────────────────

def settings_menu(cfg: dict) -> str:
    while True:
        current = cfg.get("output_path", "[not set]")
        console.print(Rule(style="cyan"))
        console.print(Panel(
            f"[bold white]Current download folder:[/]\n[url]{current}[/url]\n\n"
            f"  [muted]Videos → {current}/Videos/[/muted]\n"
            f"  [muted]Audio  → {current}/Audio/[/muted]",
            title="[title] Settings [/title]",
            border_style="blue", padding=(1, 2),
        ))
        console.print("  [bold yellow]1[/bold yellow]  Change download folder")
        console.print("  [bold yellow]b[/bold yellow]  ← Back")
        nav_hint(["1 = change folder", "b = back"])

        choice = Prompt.ask("[info]Choice[/info]", default="b").strip().lower()
        if choice == "b":
            return "back"
        if choice == "1":
            new_path = Prompt.ask("[info]New download folder[/info]",
                                  default=cfg.get("output_path", str(Path.home() / "Downloads")))
            cfg["output_path"] = new_path
            save_config(cfg)
            console.print(f"[success]✔  Updated → {new_path}[/success]")


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu(cfg: dict) -> None:
    base_path = get_output_path(cfg)

    while True:
        console.print(Rule(style="blue"))
        console.print("[info]Main Menu[/info]")
        console.print(f"  [muted]Download folder:[/muted] [url]{base_path}[/url]")
        console.print(Rule(style="blue"))
        console.print("  [bold yellow]1[/bold yellow]  Search YouTube")
        console.print("  [bold yellow]2[/bold yellow]  Direct URL  (video or playlist)")
        console.print("  [bold yellow]3[/bold yellow]  Settings")
        console.print("  [bold yellow]q[/bold yellow]  Quit")
        nav_hint(["1-3 = choose", "q = quit"])

        choice = Prompt.ask("[info]Choice[/info]", default="1").strip().lower()

        if choice == "q":
            console.print("\n[muted]Goodbye.[/muted]\n")
            break

        if choice == "1":
            signal = search_flow(base_path)
        elif choice == "2":
            signal = direct_url_flow(base_path)
        elif choice == "3":
            signal = settings_menu(cfg)
            # Reload base_path in case it was changed
            base_path = cfg.get("output_path", base_path)
        else:
            console.print("[warning]Please choose 1, 2, 3, or q.[/warning]")
            continue

        if signal == "quit":
            console.print("\n[muted]Goodbye.[/muted]\n")
            break
        # "back" → just loop to top of main menu


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    show_banner()
    cfg = load_config()
    try:
        main_menu(cfg)
    except KeyboardInterrupt:
        console.print("\n[warning]Interrupted.[/warning]\n")


if __name__ == "__main__":
    main()
