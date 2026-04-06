import sys
import json
from youtube_transcript_api import YouTubeTranscriptApi

def get_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-IN'])
        text = " ".join([entry['text'] for entry in transcript])
        print("---TRANSCRIPT_START---")
        print(text)
        print("---TRANSCRIPT_END---")
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        get_transcript(sys.argv[1])
    else:
        print("Usage: python script.py <video_id>")
