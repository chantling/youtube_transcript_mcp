# YouTube Transcript MCP Server

A Model Context Protocol (MCP) server for extracting transcripts from YouTube videos using yt-dlp.

## Features

- Extracts automatic subtitles (captions) from YouTube videos
- Supports multiple languages (default: English)
- Returns the full transcript as plain text
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
cd YTTranscript-YT-DLP-MCP

# Install the package
pip install -e .
```

Or install dependencies directly:

```bash
pip install yt-dlp srt mcp
```

## Usage

### Cline MCP Configuration

Add this configuration to your Cline MCP settings (usually in `~/.config/claude/mcp.json` or equivalent):

```json
"youtube-transcript-mcp": {
  "disabled": false,
  "timeout": 60,
  "type": "stdio",
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

```bash
python -m youtube_transcript_mcp
```

The server will listen on `127.0.0.1:8000` by default.

### Using the transcribe Tool

The server provides a `transcribe` tool that takes two arguments:

- `url` (required): The YouTube video URL or ID
- `language` (optional): Language code for subtitles (default: "en")

### Example via MCP Client (Python)

```python
import asyncio
from mcp.client.stdio import stdio_client

async def main():
    async with stdio_client() as (read_stream, write_stream):
        from mcp import Client
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
            print(f"Transcript: {result.content}")
        elif hasattr(result, 'isError') and result.isError:
            print(f"Error: {result.content}")
        else:
            print(f"Result: {result}")

asyncio.run(main())
```

## How It Works

1. **Video Information Extraction**: Uses yt-dlp to extract metadata about the YouTube video
2. **Subtitle Download**: Downloads automatic subtitles (captions) in SRT format
3. **Format Conversion**: Converts SRT to plain text using the srt library
4. **Text Assembly**: Joins all subtitle segments with spaces to form the complete transcript

The server uses the same proven approach as the [yt-dlp-transcript](https://github.com/yt-dlp/yt-dlp-transcript) library.

## Configuration

The server uses the following yt-dlp options by default:

- `skip_download`: True - Don't download the video file
- `writeautomaticsub`: True - Download automatic captions
- `subtitlesformat`: "srt" - Use SRT format
- `postprocessors`: FFmpegSubtitlesConvertor - Convert subtitles to SRT
- `retries`: 10 - Retry failed downloads up to 10 times

## Testing

Run the test script to verify everything works:

```bash
python test_transcript.py
```

You can test:
1. Direct function call (option 1)
2. Via MCP server (option 2)

## Troubleshooting

### "No subtitles" Error

- Not all videos have subtitles available
- Automatic captions may not be available for all videos
- Try a different language code (e.g., "en", "es", "de", "fr")

### ffmpeg Not Found

- Install ffmpeg from [ffmpeg.org](https://ffmpeg.org/download.html)
- Add ffmpeg to your system PATH

### Download Failed

- Check your internet connection
- Try a different video URL
- The video may have region restrictions

## License

This project is licensed under the Unlicense. See [LICENSE](LICENSE) for details.