from pytubefix import YouTube
from pytubefix.cli import on_progress
from sys import argv
import pytubefix
#gets url from input
url = input("URL >")

yt = YouTube(
        url,
        on_progress_callback=on_progress,
        use_oauth=True,
        allow_oauth_cache=True
    )

#### Gets highest res
ys = yt.streams.get_highest_resolution()



dl = pytubefix.streams.Stream.download

print(f'Title: ', {yt.title})

print (f'View: ', {yt.views})

dl(output_path="/run/media/wobbie/8TB HDD/YTDownloads/Downloaded")