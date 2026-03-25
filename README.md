# YouTube Transcript MCP Server

A Model Context Protocol (MCP) server for extracting transcripts and metadata from YouTube videos using yt-dlp.

## Features

- Extracts automatic subtitles (captions) from YouTube videos
- Extracts video metadata (title, video ID, channel name, upload date)
- Supports multiple languages (default: English)
- Returns transcript and metadata as JSON for easy LLM processing
- Uses yt-dlp's robust download mechanism
- Converts SRT subtitle format to plain text using the srt library

## Installation

### Prerequisites

- Python 3.10 or higher
- yt-dlp: `pip install yt-dlp`
- srt library: `pip install srt`
- ffmpeg (for subtitle conversion): Install from [ffmpeg.org](https://ffmpeg.org/download.html)
- mcp package: `pip install mcp`

### Install the Server

```bash
# Clone the repository
cd YoutubeTranscriptYT-DLP

# Install dependencies
pip install yt-dlp srt mcp
```

**Note:** This package uses a wrapper script approach for easy integration. The `youtube_transcript_mcp.py` file in the parent directory handles importing the package and running the MCP server.

### Direct Import (for development)

If you want to import and use the functions directly in your Python code:

```python
import sys
import os

# Add the package directory to Python path
sys.path.insert(0, r'D:\Programs\AI\!MCPServers!\YoutubeTranscriptYT-DLP\youtube_transcript_mcp')

from youtube_transcript_mcp import extract_youtube_transcript, transcribe
```

Or install dependencies directly:

```bash
pip install yt-dlp srt mcp
```

## Usage

### Cline MCP Configuration

Add this configuration to your Cline MCP settings (usually in `~/.config/claude/mcp.json` or equivalent):

**Option 1: Using the wrapper script (recommended)**
```json
"youtube-transcript-mcp": {
  "disabled": false,
  "timeout": 60,
  "command": "C:\\Users\\John\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
  "args": [
    "D:\\Programs\\AI\\!MCPServers\\!YoutubeTranscriptYT-DLP\\youtube_transcript_mcp.py"
  ],
  "env": {
    "FFMPEG_LOCATION": "C:\\Users\\John\\AppData\\Local\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\\ffmpeg-8.0.1-full_build\\bin\\ffmpeg.exe"
  }
}
```

**Option 2: Using package module (requires full package installation)**
```json
"youtube-transcript-mcp": {
  "disabled": false,
  "timeout": 60,
  "command": "python",
  "args": [
    "-m",
    "youtube_transcript_mcp"
  ],
  "env": {
    "FFMPEG_LOCATION": "{Path to FFMPEG}"
  }
}
```

Replace `{Path to FFMPEG}` with the actual path to your ffmpeg installation (e.g., `C:\ffmpeg\bin` on Windows or `/usr/local/bin` on macOS/Linux).

### Starting the Server

**Using the wrapper script:**
```bash
python youtube_transcript_mcp.py
```

**Using the package module (if installed):**
```bash
python -m youtube_transcript_mcp
```

The server runs in stdio mode and communicates via standard input/output. No web server is started.

### Using the transcribe Tool

The server provides a `transcribe` tool with the following arguments:

- `url` (required): The YouTube video URL or ID
- `language` (optional): Language code for subtitles (default: "en")
- `offset` (optional): Character offset to start reading from (default: `0`). Only used when `limit > 0`.
- `limit` (optional): Max characters per chunk when paginating (default: `0` = return full transcript as multiple content items)

#### Default Behavior (limit=0)

By default, the tool returns the **full transcript in a single call** as multiple content items:

- **Item 1**: JSON with video metadata (`video_id`, `title`, `channel`, `date_posted`, `transcript_length`)
- **Item 2+**: Plain-text transcript chunks (~50,000 characters each, aligned to sentence boundaries)

No pagination is required — the agent receives everything at once.

#### Paginated Behavior (limit > 0)

When `limit` is set, the tool returns a single JSON dict with one transcript chunk and pagination fields. Use this if you need fine-grained control over chunk size.

When `limit > 0`, additional pagination fields are included:

- `transcript_length`: Total character count of the full transcript
- `transcript_offset`: Suggested offset for the next chunk (aligned to a sentence boundary)
- `transcript_remaining`: Characters remaining after this chunk

### Using the flush_cache Tool

The `flush_cache` tool deletes all cached transcript files from the local cache. Call this when you are finished reading a transcript to free disk space.

Cache files also expire automatically after 1 hour and are cleaned up on server startup, so calling `flush_cache` is optional but recommended.

```
# After you finish paginating through a transcript:
flush_cache()
# Returns: {"deleted": 1}
```

### Caching

Transcripts are cached locally after the first download. Subsequent paginated calls for the same `(url, language)` pair serve from the cache file — no additional requests are made to YouTube. This prevents unnecessary strain on YouTube's servers and avoids rate-limiting or IP bans.

Cache files are stored in the system temp directory under `youtube_transcript_cache/` and expire after 1 hour.

### Reading Long Transcripts

By default, the tool returns the full transcript as multiple content items in a single call. Simply call:

```
transcribe(url="https://youtube.com/watch?v=VIDEO_ID")
```

You will receive:
- Item 1: JSON metadata (`{"video_id": "...", "title": "...", "channel": "...", "date_posted": "...", "transcript_length": 45000}`)
- Item 2: First ~50,000 characters of transcript (plain text, sentence-aligned)
- Item 3: Next ~50,000 characters
- ...and so on until the full transcript is delivered

No pagination is needed. Call `flush_cache()` when finished.

#### Manual Pagination (limit > 0)

For fine-grained control, use `offset` and `limit` to retrieve the transcript in smaller chunks:

```
# First call: get the first ~1900 characters
transcribe(url="https://youtube.com/watch?v=LONG_VIDEO", limit=1900)

# Response includes:
#   "transcript": "Today's number 40..."
#   "transcript_length": 45000
#   "transcript_offset": 1893
#   "transcript_remaining": 43107

# Second call: use transcript_offset as the next offset
transcribe(url="https://youtube.com/watch?v=LONG_VIDEO", offset=1893, limit=1900)

# Continue until transcript_remaining == 0, then:
flush_cache()
```

### Example via MCP Client (Python)

```python
import asyncio
import json
from mcp.client.stdio import stdio_client
from mcp import Client

async def main():
    async with stdio_client() as (read_stream, write_stream):
        client = Client("stdio-server", read_stream, write_stream)
        
        # Call the transcribe tool
        result = await client.call_tool(
            "transcribe",
            arguments={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "language": "en"
            }
        )
        
        if hasattr(result, 'content'):
            data = json.loads(result.content[0].text)
            print(f"Video Title: {data['title']}")
            print(f"Channel: {data['channel']}")
            print(f"Date: {data['date_posted']}")
            print(f"Transcript: {data['transcript'][:200]}...")
        elif hasattr(result, 'isError') and result.isError:
            print(f"Error: {result.content}")
        else:
            print(f"Result: {result}")

asyncio.run(main())
```

## How It Works

1. **Metadata Extraction**: Uses yt-dlp's `extract_info()` to retrieve video metadata (title, ID, channel, upload date)
2. **Subtitle Download**: Downloads automatic subtitles (captions) in SRT format
3. **Format Conversion**: Converts SRT to plain text using the srt library
4. **Text Assembly**: Joins all subtitle segments with spaces to form the complete transcript
5. **JSON Response**: Returns metadata and transcript as a structured JSON object

The server uses the same proven approach as the [yt-dlp-transcript](https://github.com/yt-dlp/yt-dlp-transcript) library.

## Workflow Example: LLM + Obsidian

This server is designed to work seamlessly with LLMs and knowledge management tools like Obsidian. Here's a typical workflow:

### 1. Extract Video Content
```python
# Call the MCP transcribe tool
result = await client.call_tool(
    "transcribe",
    arguments={"url": "https://www.youtube.com/watch?v=VIDEO_ID"}
)
data = json.loads(result.content[0].text)
```

### 2. Process with LLM
Pass the JSON data to an LLM to:
- Summarize the transcript
- Extract key points and insights
- Generate tags or categories
- Create markdown-formatted notes

Example prompt to LLM:
```
Please summarize this YouTube video content:

Title: {data['title']}
Channel: {data['channel']}
Date: {data['date_posted']}

Transcript:
{data['transcript']}

Please provide:
1. A concise summary (2-3 sentences)
2. 5-7 key takeaways as bullet points
3. Relevant tags
4. Output in Markdown format
```

### 3. Store in Obsidian
Use the LLM's markdown output to create an Obsidian note:

```markdown
# {video_title}

**Source:** [YouTube](https://www.youtube.com/watch?v={video_id})
**Channel:** {channel_name}
**Date:** {readable_date}

## Summary
{llm_summary}

## Key Takeaways
- {takeaway_1}
- {takeaway_2}
- {takeaway_3}

## Tags
#youtube #video #learning #{channel_name}
```

This workflow ensures you capture both the video metadata and processed insights in your knowledge base.

## Response Format

### Default (limit=0): Multiple Content Items

The tool returns multiple content items in a single call:

**Item 1** — Metadata (JSON):
```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
  "channel": "Rick Astley",
  "date_posted": "20091025",
  "transcript_length": 12345
}
```

**Item 2+** — Plain-text transcript chunks (~50K chars each, sentence-aligned):
```
We're no strangers to love. You know the rules and so do I...
```

### Paginated (limit > 0): Single JSON Object

```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
  "channel": "Rick Astley",
  "date_posted": "20091025",
  "transcript": "We're no strangers to love. You know the rules and so do I...",
  "transcript_length": 45000,
  "transcript_offset": 1893,
  "transcript_remaining": 43107
}
```

### Metadata Fields

- **video_id**: The unique YouTube video identifier (e.g., "dQw4w9WgXcQ")
- **title**: Full video title as shown on YouTube
- **channel**: Name of the channel or uploader
- **date_posted**: Upload date in YYYYMMDD format (e.g., "20091025" = October 25, 2009)
- **transcript_length**: Total character count of the full transcript
- **transcript** *(paginated only)*: Chunk of transcript text
- **transcript_offset** *(paginated only)*: Suggested offset for the next chunk
- **transcript_remaining** *(paginated only)*: Characters remaining after this chunk

### Notes on Date Format

The `date_posted` field uses the ISO 8601 date format (YYYYMMDD). To convert to a more readable format:

```python
from datetime import datetime
date_posted = "20091025"
readable_date = datetime.strptime(date_posted, "%Y%m%d").strftime("%B %d, %Y")
# Output: "October 25, 2009"
```

## Moving the Package

If you move the `youtube_transcript_mcp` folder to a new location, the wrapper script `youtube_transcript_mcp.py` will automatically work because it uses a relative path. Simply update the path in your MCP configuration to point to the new location of the wrapper script.

Example configuration after moving the folder:
```json
"youtube-transcript-mcp": {
  "command": "C:\\Users\\John\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
  "args": [
    "D:\\New\\Path\\To\\youtube_transcript_mcp.py"  # Update this path
  ]
}
```

## API Considerations

### Performance

- Metadata extraction adds minimal overhead (~100-500ms) to each request
- Subtitle download time varies based on video length and caption complexity
- Consider caching results locally if you process the same videos repeatedly

### Rate Limiting

- YouTube may rate-limit requests if you make too many in quick succession
- Implement delays between requests if processing multiple videos
- yt-dlp automatically retries failed requests (up to 10 times)

### Language Support

- Not all videos have subtitles in all languages
- Language codes follow ISO 639-1 format (e.g., "en", "es", "de", "fr", "ja")
- Automatic captions are generally available in English for most videos
- Manual subtitles may not be available for download

### Data Privacy

- The server only communicates with YouTube's public API
- No user data or authentication is required
- Transcripts are processed locally and never transmitted to external services (except via your LLM)

## Configuration

The server uses the following yt-dlp options by default:

- `skip_download`: True - Don't download the video file
- `writeautomaticsub`: True - Download automatic captions
- `subtitlesformat`: "srt" - Use SRT format
- `postprocessors`: FFmpegSubtitlesConvertor - Convert subtitles to SRT
- `retries`: 10 - Retry failed downloads up to 10 times

## Testing

Run the example script to verify everything works:

```bash
python example_usage.py
```

This script demonstrates:
- Extracting transcript and metadata from a video
- Parsing the JSON response
- Accessing individual metadata fields
- Example workflow for LLM processing

## Troubleshooting

### "No subtitles" Error

- Not all videos have subtitles available
- Automatic captions may not be available for all videos
- Try a different language code (e.g., "en", "es", "de", "fr")
- Some videos may have manual subtitles but no automatic captions

### Missing Metadata Fields

Some videos may have incomplete metadata. The server provides empty strings for missing fields:

```json
{
  "video_id": "",
  "title": "Video unavailable",
  "channel": "",
  "date_posted": "",
  "transcript": "..."
}
```

This can occur if:
- The video has been deleted or made private
- The channel has been terminated
- Regional restrictions prevent metadata access

### ffmpeg Not Found

- Install ffmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
- Add ffmpeg to your system PATH
- Set `FFMPEG_LOCATION` environment variable if ffmpeg is not in PATH

### Download Failed

- Check your internet connection
- Try a different video URL
- The video may have region restrictions
- The video may require age verification
- YouTube API rate limits may temporarily block requests

### Date Format Issues

The `date_posted` field uses YYYYMMDD format. If you need a different format, convert it programmatically:

```python
# YYYYMMDD to ISO 8601 (YYYY-MM-DD)
date_posted = "20091025"
iso_date = f"{date_posted[:4]}-{date_posted[4:6]}-{date_posted[6:]}"

# YYYYMMDD to readable format
from datetime import datetime
readable_date = datetime.strptime(date_posted, "%Y%m%d").strftime("%B %d, %Y")
```

## License

This project is licensed under the Unlicense. See [LICENSE](LICENSE) for details.