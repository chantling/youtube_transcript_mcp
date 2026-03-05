#!/usr/bin/env python3
"""YouTube Transcript MCP Server"""

import os
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp.server.fastmcp import FastMCP
import srt
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Default options matching reference implementation
default_opts = {
    "no_warnings": True,
    "noprogress": True,
    "postprocessors": [
        {"format": "srt", "key": "FFmpegSubtitlesConvertor", "when": "before_dl"},
    ],
    "quiet": True,
    "retries": 10,
    "skip_download": True,
    "writeautomaticsub": True,
}

# Add ffmpeg_location if environment variable is set
if os.getenv("FFMPEG_LOCATION"):
    default_opts["ffmpeg_location"] = os.getenv("FFMPEG_LOCATION")


def extract_youtube_transcript(url: str, language: str = "en") -> dict:
    """
    Extract transcript and metadata from a YouTube video URL.

    Args:
        url: YouTube video URL or ID
        language: Language code for subtitles (default: "en")

    Returns:
        Dictionary containing video metadata and transcript:
        - video_id: YouTube video ID
        - title: Video title
        - channel: Channel/uploader name
        - date_posted: Upload date (format: YYYYMMDD)
        - transcript: Full transcript text

    Raises:
        ValueError: If URL is invalid
        DownloadError: If transcript cannot be downloaded
    """
    if not url or not isinstance(url, str):
        raise ValueError("Invalid YouTube URL provided")

    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        opts = deepcopy(default_opts)
        opts["outtmpl"] = {"default": str(path / "res")}
        opts["subtitleslangs"] = [language]

        # Extract metadata first
        ydl = YoutubeDL(opts)
        info = ydl.extract_info(url, download=False)

        # Extract metadata fields
        metadata = {
            "video_id": info.get("id", ""),
            "title": info.get("title", ""),
            "channel": info.get("uploader", ""),
            "date_posted": info.get("upload_date", ""),
        }

        # Download subtitles
        ydl.download(url)

        # Find SRT files
        srt_files = list(path.glob("*.srt"))

        if len(srt_files) < 1:
            raise DownloadError(
                f"Error: cannot download subtitles for {url}, "
                f"probably the video has no subtitles."
            )

        # Parse SRT file directly
        with open(srt_files[0], "r", encoding="utf-8") as f:
            subtitles = list(srt.parse(f))

        # Build transcript by joining all subtitle text
        transcript = " ".join([s.content.replace("\n", " ") for s in subtitles])

        return {**metadata, "transcript": transcript}


# Create MCP server
mcp = FastMCP("youtube-transcript-mcp")


# Add transcribe tool
@mcp.tool()
async def transcribe(url: str, language: str = "en") -> dict:
    """
    Extract transcript and metadata from a YouTube video.

    Args:
        url: YouTube video URL (e.g., https://www.youtube.com/watch?v=VIDEO_ID)
        language: Language code for subtitles (default: "en")

    Returns:
        Dictionary containing:
        - video_id: YouTube video ID
        - title: Video title
        - channel: Channel/uploader name
        - date_posted: Upload date (format: YYYYMMDD)
        - transcript: Full transcript text

    Example:
        transcribe("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        Returns: {"video_id": "dQw4w9WgXcQ", "title": "...", "channel": "...",
                 "date_posted": "20091025", "transcript": "When the sun shines..."}
    """
    try:
        result = extract_youtube_transcript(url, language)
        return result
    except DownloadError as e:
        raise ValueError(f"Failed to download transcript: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error extracting transcript: {str(e)}")


def main():
    """Run MCP server"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
