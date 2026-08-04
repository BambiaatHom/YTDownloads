#!/usr/bin/env python3
"""🔍 CORE YOUTUBE DOWNLOAD LOGIC USING YT-DLP - Clean & Simple 🔥"""

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yt_dlp


def get_video_info(url: str) -> Dict:
    """Get YouTube video metadata using yt-dlp"""
    ydl_opts = {'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            'title': info.get('title', ''),
            'uploader': info.get('uploader', ''),
            'duration': info.get('duration', 0),
            'view_count': info.get('view_count', 0),
            'upload_date': info.get('upload_date', '')
        }


def search_youtube(query: str, limit: int = 10) -> List[Dict]:
    """Search YouTube using yt-dlp"""
    ydl_opts = {
        'quiet': True,
        'extract_flat': False,
        'noplaylist': True
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        results = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
        
        videos = []
        if 'entries' in results:
            for entry in results['entries']:
                if entry:
                    videos.append({
                        'title': entry.get('title', ''),
                        'uploader': entry.get('uploader', ''),
                        'duration': entry.get('duration', 0),
                        'url': f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                    })
        
        return videos[:limit]


def is_valid_url(text: str) -> bool:
    """Check if text looks like a YouTube URL"""
    return (text.startswith('http://') or 
            text.startswith('https://') or 
            'youtube.com' in text or 
            'youtu.be' in text)


def download_audio(url_or_query: str, output_path: Path, format_choice: str = "mp3") -> Optional[Path]:
    """Download audio using yt-dlp - Clean & Simple"""
    
    fmt_map = {
        "1": ("bestaudio", "mp3", "320"),
        "2": ("bestaudio", "mp3", "192"),
        "3": ("bestaudio", "wav", ""),
        "4": ("bestaudio", "flac", ""),
        "5": ("bestaudio", "ogg", "256"),
        "6": ("bestaudio", "m4a", "")
    }
    
    codec, ext, quality = fmt_map.get(format_choice, ("bestaudio", "mp3", "320"))
    
    # 🔥 YT-DLP MANUAL RECOMMENDED FORMAT!
    ydl_opts = {
        'format': f'{codec}/bestaudio/best',  # Flexible fallback chain!
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': ext,
            'preferredquality': quality if quality else "0"
        }],
        'quiet': False,
        'no_warnings': True,
        'timeout': 300,
        # No metadata - clean download!
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if is_valid_url(url_or_query):
                # It's a URL, download directly
                ydl.download([url_or_query])
            else:
                # Search YouTube first for the query
                results = ydl.extract_info(f'ytsearch1:{url_or_query}', download=False)
                
                if 'entries' in results and results['entries']:
                    video_url = f"https://www.youtube.com/watch?v={results['entries'][0]['id']}"
                    ydl.download([video_url])
        
        files = list(output_path.glob(f'*.{ext}'))
        if files:
            return files[0]
        return None
        
    except Exception as e:
        print(f"[danger]Download failed: {e}")
        return None


def download_video(url: str, output_path: Path) -> Optional[Path]:
    """Download video using yt-dlp"""
    
    ydl_opts = {
        'format': 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]',
        'outtmpl': str(output_path / '%(title)s.%(ext)s'),
        'quiet': False,
        'no_warnings': True,
        'timeout': 300,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        files = list(output_path.glob('*.mp4'))
        if files:
            return files[0]
        return None
        
    except Exception as e:
        print(f"[danger]Video download failed: {e}")
        return None


def get_bulk_format_choice() -> str:
    """Get format choice for bulk downloads"""
    from rich.prompt import Prompt
    
    print("\n[info]Choose download format for all tracks:[/info]")
    print("  [bold yellow]1[/bold yellow]  MP3 320kbps")
    print("  [bold yellow]2[/bold yellow]  MP3 192kbps")
    print("  [bold yellow]3[/bold yellow]  WAV")
    print("  [bold yellow]4[/bold yellow]  FLAC")
    print("  [bold yellow]5[/bold yellow]  OGG")
    print("  [bold yellow]6[/bold yellow]  M4A")
    
    choice = Prompt.ask("[info]Format", default="1")
    return choice if choice in ["1", "2", "3", "4", "5", "6"] else None


def fmt_duration(seconds) -> str:
    """Format duration nicely"""
    h, r = divmod(int(seconds or 0), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def fmt_views(n) -> str:
    """Format view count nicely"""
    if n is None: return "N/A"
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_size(b) -> str:
    """Format file size nicely"""
    if b is None: return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"
