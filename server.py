#!/usr/bin/env python3
"""YouTube Transcript MCP Server"""

import os
from copy import deepcopy
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import List

from mcp.server.fastmcp import FastMCP
import srt
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


def deduplicate_subtitles(subtitles: List[srt.Subtitle]) -> List[srt.Subtitle]:
    """
    Remove duplicate and rolling subtitles from YouTube auto-generated captions.
    
    YouTube's automatic captions use a "rolling" format where when a new line appears,
    the previous line moves up. This causes duplicate lines in the downloaded transcripts.
    
    This function:
    1. Detects identical consecutive subtitles and merges them
    2. Detects rolling patterns (previous text = first line of current subtitle)
    3. Returns a clean list of unique subtitles with correct timing
    
    Args:
        subtitles: List of parsed SRT subtitle objects
        
    Returns:
        List of deduplicated subtitle objects
    """
    if not subtitles:
        return []
    
    deduped = []
    previous_subtitle = None
    previous_text_clean = ""
    
    for sub in subtitles:
        # Clean current subtitle text (remove extra whitespace, normalize)
        current_text_clean = sub.content.strip()
        current_lines = [line.strip() for line in current_text_clean.split('\n') if line.strip()]
        
        if previous_subtitle is None:
            # First subtitle, always keep it
            deduped.append(sub)
            previous_subtitle = sub
            previous_text_clean = current_text_clean
            continue
        
        # Check for exact duplicate (current text == previous text)
        if current_text_clean == previous_text_clean:
            # Extend the previous subtitle's end time to include this one
            deduped[-1] = srt.Subtitle(
                index=deduped[-1].index,
                start=deduped[-1].start,
                end=sub.end,  # Extend end time
                content=deduped[-1].content
            )
            continue
        
        # Check for rolling pattern: previous text equals first line of current
        if current_lines and current_lines[0] == previous_text_clean:
            # Rolling subtitle - extract the new content (second line onwards)
            new_content = '\n'.join(current_lines[1:]) if len(current_lines) > 1 else ""
            
            if new_content:
                # Create a new subtitle with just the new content
                new_sub = srt.Subtitle(
                    index=len(deduped) + 1,
                    start=sub.start,
                    end=sub.end,
                    content=new_content
                )
                deduped.append(new_sub)
                previous_subtitle = new_sub
                previous_text_clean = new_content
            continue
        
        # Check if previous text equals first part of multiline current text
        if '\n' in current_text_clean:
            # Current subtitle has multiple lines
            if current_lines[0] == previous_text_clean:
                # Rolling pattern detected - keep only the new lines
                new_content = '\n'.join(current_lines[1:])
                if new_content:
                    new_sub = srt.Subtitle(
                        index=len(deduped) + 1,
                        start=sub.start,
                        end=sub.end,
                        content=new_content
                    )
                    deduped.append(new_sub)
                    previous_subtitle = new_sub
                    previous_text_clean = new_content
            else:
                # Normal subtitle with multiple lines
                deduped.append(sub)
                previous_subtitle = sub
                previous_text_clean = current_text_clean
        else:
            # Normal single-line subtitle
            deduped.append(sub)
            previous_subtitle = sub
            previous_text_clean = current_text_clean
    
    # Re-index subtitles sequentially
    for i, sub in enumerate(deduped, start=1):
        deduped[i-1] = srt.Subtitle(
            index=i,
            start=sub.start,
            end=sub.end,
            content=sub.content
        )
    
    return deduped

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

        # Deduplicate subtitles to remove YouTube's rolling caption duplicates
        subtitles = deduplicate_subtitles(subtitles)

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
