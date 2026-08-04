#!/usr/bin/env python3
"""🎵 SPOTIFY API INTEGRATION MANAGER - Your Music Collection with Metadata"""

from pathlib import Path
from typing import Dict, List, Optional


try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False


class SpotifyManager:
    """Handles all Spotify API interactions - your music curator"""
    
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sp = None
        
    def setup_spotify(self) -> bool:
        """Set up Spotify connection for you with dynamic port"""
        if not SPOTIFY_AVAILABLE:
            print("[danger]Spotify integration not available![/danger]")
            return False
            
        import socket
        
        def get_free_port():
            with socket.socket() as s:
                s.bind(('', 0))
                return s.getsockname()[1]
        
        callback_port = get_free_port()
        callback_uri = f"http://127.0.0.1:{callback_port}/callback"
        
        print(Panel(
            "[info]Spotify Setup[/info]\n"
            "You'll need Spotify API credentials:\n"
            "1. Go to https://developer.spotify.com/dashboard\n"
            "2. Create an app\n"
            "3. Get your Client ID and Client Secret\n"
            f"4. Set redirect URI to: {callback_uri}",
            border_style="cyan"
        ))
        
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
        else:
            client_id = Prompt.ask("[info]Spotify Client ID[/info]")
            client_secret = Prompt.ask("[info]Spotify Client Secret[/info]", password=True)
            
            if Confirm.ask("[info]Save credentials for next time?[/info]", default=True):
                self.cfg["spotify_client_id"] = client_id
                self.cfg["spotify_client_secret"] = client_secret
        
        try:
            scope = "user-library-read playlist-read-private playlist-read-collaborative user-top-read user-follow-read"
            
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=callback_uri,  # Use dynamic port!
                scope=scope,
                open_browser=True
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
            user = self.sp.current_user()
            print(f"[success]✔ Connected to Spotify as: {user['display_name']}[/success]")
            
            return True
            
        except Exception as e:
            print(f"[danger]Failed to connect to Spotify: {e}[/danger]")
            return False
    
    def get_liked_songs(self, limit: int = 50) -> List[Dict]:
        """Get your liked songs"""
        if not self.sp:
            return []
        
        songs = []
        offset = 0
        batch_size = 50
        
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
            
            if len(songs) >= limit or not results['next']:
                break
            
            offset += batch_size
        
        return songs[:limit] if limit < 999999 else songs
    
    def get_playlists(self) -> List[Dict]:
        """Get your playlists"""
        if not self.sp:
            return []
        
        playlists = []
        results = self.sp.current_user_playlists()
        for item in results['items']:
            if item:
                playlists.append({
                    'name': item.get('name', 'Untitled'),
                    'id': item.get('id', ''),
                    'track_count': item.get('tracks', {}).get('total', 0)
                })
        
        return playlists
    
    def get_playlist_tracks(self, playlist_id: str) -> List[Dict]:
        """Get tracks from a specific playlist"""
        if not self.sp:
            return []
        
        songs = []
        try:
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
            
            return songs
            
        except Exception as e:
            print(f"[warning]Error accessing playlist: {e}[/warning]")
            return []
        
        return songs
    
    def get_top_tracks(self, limit: int = 50) -> List[Dict]:
        """Get your top tracks"""
        if not self.sp:
            return []
        
        songs = []
        results = self.sp.current_user_top_tracks(limit=limit)
        for item in results['items']:
            songs.append({
                'name': item['name'],
                'artist': item['artists'][0]['name'],
                'search_query': f"{item['artists'][0]['name']} - {item['name']}"
            })
        
        return songs
    
    def get_top_artists(self, limit: int = 20) -> List[Dict]:
        """Get your top artists"""
        if not self.sp:
            return []
        
        artists = []
        results = self.sp.current_user_top_artists(limit=limit)
        for item in results['items']:
            artists.append({
                'name': item['name'],
                'genres': item['genres'][:3] if item['genres'] else []
            })
        
        return artists
    
    def get_followed_artists(self) -> List[Dict]:
        """Get your followed artists"""
        if not self.sp:
            return []
        
        artists = []
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
        """Get albums from an artist"""
        if not self.sp:
            return []
        
        albums = []
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
        """Get tracks from an album"""
        if not self.sp:
            return []
        
        songs = []
        results = self.sp.album_tracks(album_id)
        for item in results['items']:
            songs.append({
                'name': item['name'],
                'artist': item['artists'][0]['name'],
                'search_query': f"{item['artists'][0]['name']} - {item['name']}"
            })
        
        return songs


from rich.panel import Panel
from rich.prompt import Prompt, Confirm
