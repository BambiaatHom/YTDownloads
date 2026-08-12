{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  name = "ytdlp-downloader-env";

  buildInputs = [
    pkgs.ffmpeg
    pkgs.nodejs_24
    
    (pkgs.python3.withPackages (ps: [
      ps.yt-dlp
      ps.rich
      ps.mutagen
      ps.plyer
      ps.spotipy
    ]))
  ];

  shellHook = ''
    echo "=========================================================="
    echo "▶ Ready to build your new yt-dlp App on NixOS!"
    echo "=========================================================="
  '';
}

