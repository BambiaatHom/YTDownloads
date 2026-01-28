from pytubefix import YouTube
from pytubefix.cli import on_progress


url = input("URL > ")

#Create a YT object
yt = YouTube(url, on_progress_callback=on_progress, use_oauth=True,
        allow_oauth_cache=True)
print(f"Downloading: {yt.title}")



#####Gets highest res stream
stream = yt.streams.get_highest_resolution()



#####Downloads the video
stream.download(output_path="/run/media/wobbie/8TB HDD/YTDownloads/Downloaded")


###### Get audio-only stream
# audio_stream = yt.streams.get_audio_only()

####### Download as m4a
# audio_stream.download(output_path="/run/media/wobbie/8TB HDD/YTDownloads/Downloaded")

print("Download complete!")