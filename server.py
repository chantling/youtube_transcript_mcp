#!/usr/bin/env python3
"""YouTube Transcript MCP Server"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any, List

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
import srt
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

CONFIG_KEYS = {"cookies_from_browser", "cookies_file", "js_runtime", "pot_server_home"}


def _load_config() -> dict[str, str]:
    config_path = os.getenv("MCP_CONFIG_PATH", "")
    if not config_path:
        config_path = str(Path(__file__).resolve().parent / "config.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    cfg: dict[str, str] = {}
    for key in CONFIG_KEYS:
        env_val = os.getenv(f"MCP_{key.upper()}", "")
        if env_val:
            cfg[key] = env_val
        elif data.get(key):
            cfg[key] = str(data[key])
    return cfg


def _detect_pot_server_home(config: dict[str, str]) -> str | None:
    """
    Locate the bgutil-ytdlp-pot-provider server directory.

    Uses the pot_server_home config value if set; otherwise checks for a
    bgutil-ytdlp-pot-provider checkout next to this project (i.e.
    <project_root>/bgutil-ytdlp-pot-provider/server). Returns None when no
    provider with a built main.js HTTP server is found.
    """
    candidates = []
    if config.get("pot_server_home"):
        candidates.append(Path(config["pot_server_home"]))
    project_root = Path(__file__).resolve().parent.parent
    candidates.append(project_root / "bgutil-ytdlp-pot-provider" / "server")
    for candidate in candidates:
        if (candidate / "build" / "main.js").is_file():
            return str(candidate)
    return None


def _apply_ytdlp_opts(base_opts: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    if config.get("cookies_from_browser"):
        base_opts["cookiesfrombrowser"] = (config["cookies_from_browser"],)
    elif config.get("cookies_file"):
        base_opts["cookiefile"] = config["cookies_file"]
    if config.get("js_runtime"):
        base_opts["js_runtimes"] = {config["js_runtime"]: {}}
    # The bgutil PO token provider runs as a local HTTP server on
    # 127.0.0.1:4416 (see _ensure_pot_server_started). The yt-dlp plugin's
    # default base_url matches, so no extractor_args are needed here. The
    # script-mode provider is deliberately NOT configured: its 15s
    # availability-check timeout can crash extraction when node cold-starts
    # slowly (e.g. after the OS file cache has been evicted overnight).
    return base_opts


_ytdlp_config = _load_config()

# ---------------------------------------------------------------------------
# bgutil PO token HTTP server management
# ---------------------------------------------------------------------------

POT_BASE_URL = "http://127.0.0.1:4416"
POT_STARTUP_TIMEOUT = 60.0  # seconds to wait for a cold node start

# Handle to the background thread that starts the POT HTTP server.
# Set in main(); joined by the first transcript request so a cold node
# start (~30s after overnight file-cache eviction) completes before
# extraction instead of racing it.
_pot_server_thread: threading.Thread | None = None


def _ping_pot_server(timeout: float = 2.0) -> bool:
    """Return True if the bgutil POT HTTP server answers /ping."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{POT_BASE_URL}/ping", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_pot_server_started() -> bool:
    """
    Make sure the bgutil POT HTTP server is running on 127.0.0.1:4416.

    Reuses an already-running instance (possibly left over from a previous
    server session) so the warm process survives MCP server restarts. If
    none is running, spawns `node build/main.js` detached from this
    process and waits for it to answer /ping.

    Returns True if the server is (or becomes) reachable.
    """
    if _ping_pot_server():
        print("[pot] bgutil HTTP server already running, reusing", file=sys.stderr)
        return True

    server_home = _detect_pot_server_home(_ytdlp_config)
    if not server_home:
        print("[pot] bgutil provider not found, PO tokens unavailable", file=sys.stderr)
        return False
    main_js = Path(server_home) / "build" / "main.js"

    print(f"[pot] starting bgutil HTTP server: node {main_js}", file=sys.stderr)
    creationflags = subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(
            ["node", str(main_js)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError as e:
        print(f"[pot] failed to start bgutil HTTP server: {e}", file=sys.stderr)
        return False

    deadline = time.time() + POT_STARTUP_TIMEOUT
    while time.time() < deadline:
        if _ping_pot_server():
            print("[pot] bgutil HTTP server is up", file=sys.stderr)
            return True
        if proc.poll() is not None:
            # Process exited — most likely the port is already bound by
            # another instance that is still warming up. Give it one
            # last generous ping.
            return _ping_pot_server(timeout=10.0)
        time.sleep(1.0)

    print("[pot] bgutil HTTP server did not come up in time", file=sys.stderr)
    return False


def _wait_for_pot_server() -> None:
    """Block until the POT server startup thread finishes (best effort)."""
    thread = _pot_server_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=POT_STARTUP_TIMEOUT + 5.0)

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------

CACHE_DIR_NAME = "youtube_transcript_cache"
CACHE_TTL_SECONDS = 3600  # 1 hour

# ---------------------------------------------------------------------------
# Response chunking configuration
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE = 50000  # Characters per TextContent block


def _cache_dir() -> Path:
    """Return the cache directory path, creating it if needed."""
    d = Path(gettempdir()) / CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(url: str, language: str, timestamps: bool = False) -> str:
    """Build a deterministic cache filename from (url, language, timestamps)."""
    raw = f"{url}|{language}|{timestamps}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest() + ".json"


def clean_cache() -> int:
    """
    Delete cache files older than CACHE_TTL_SECONDS.

    Called automatically on server startup so stale entries left behind by
    crashed or misbehaving agents do not accumulate.

    Returns:
        Number of files deleted.
    """
    deleted = 0
    cutoff = time.time() - CACHE_TTL_SECONDS
    try:
        for f in _cache_dir().iterdir():
            if f.is_file() and f.suffix == ".json":
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    deleted += 1
    except FileNotFoundError:
        pass
    return deleted


def _flush_cache() -> int:
    """
    Delete ALL cached transcript files.

    Returns:
        Number of files deleted.
    """
    deleted = 0
    try:
        for f in _cache_dir().iterdir():
            if f.is_file() and f.suffix == ".json":
                f.unlink(missing_ok=True)
                deleted += 1
    except FileNotFoundError:
        pass
    return deleted


def _save_to_cache(cache_file: Path, metadata: dict, full_transcript: str) -> None:
    """Write transcript + metadata to a cache file."""
    payload = {
        "timestamp": time.time(),
        "metadata": metadata,
        "transcript": full_transcript,
    }
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_file)


def _load_from_cache(cache_file: Path) -> tuple[dict, str] | None:
    """
    Load transcript + metadata from cache if the file exists and is fresh.

    Returns:
        (metadata, full_transcript) tuple, or None if cache miss.
    """
    try:
        if not cache_file.exists():
            return None
        age = time.time() - cache_file.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            cache_file.unlink(missing_ok=True)
            return None
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data["metadata"], data["transcript"]
    except (json.JSONDecodeError, KeyError, OSError):
        cache_file.unlink(missing_ok=True)
        return None


def _format_chunk(text: str) -> str:
    """
    Add newlines after sentence terminators so each sentence is on its own line.

    This prevents tools with per-line character limits (e.g. Read at 2000
    chars/line) from truncating long transcript chunks.
    """
    # Replace sentence-ending space (or end-of-string) with newline
    result = text.replace(". ", ".\n")
    result = result.replace("? ", "?\n")
    result = result.replace("! ", "!\n")
    return result


def _split_into_text_content(
    metadata: dict,
    full_transcript: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[TextContent]:
    """
    Split transcript into multiple TextContent items for a single MCP response.

    The first TextContent contains the video metadata as JSON. Subsequent
    items contain plain-text transcript chunks with newlines at sentence
    boundaries so tools with per-line limits (e.g. Read at 2000 chars/line)
    do not truncate the content.

    Returning a list that includes at least one ContentBlock causes FastMCP
    to keep each item as a separate content block in the tool result — the
    agent receives everything in one call with no pagination round-trips.

    Args:
        metadata: Video metadata dict (video_id, title, channel, date_posted)
        full_transcript: The complete transcript text
        chunk_size: Max characters per text chunk (default 50000)

    Returns:
        List of TextContent objects ready to return from the tool.
    """
    transcript_length = len(full_transcript)

    # First item: metadata as JSON
    meta_payload = {
        **metadata,
        "transcript_length": transcript_length,
    }
    items: list[TextContent] = [
        TextContent(type="text", text=json.dumps(meta_payload, ensure_ascii=False))
    ]

    # Subsequent items: transcript in plain-text chunks with sentence-level newlines
    offset = 0
    while offset < transcript_length:
        raw_end = min(offset + chunk_size, transcript_length)
        adjusted_end = find_sentence_boundary(full_transcript, raw_end)
        chunk = full_transcript[offset:adjusted_end]
        items.append(TextContent(type="text", text=_format_chunk(chunk)))
        offset = adjusted_end

    return items


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


def find_sentence_boundary(text: str, target_pos: int, fallback_window: int = 200) -> int:
    """
    Find the last sentence boundary at or before target_pos.

    Scans backwards from target_pos looking for a sentence terminator
    (. ? !). If none found within fallback_window characters, falls back
    to the last word boundary (space).

    Args:
        text: The full transcript text
        target_pos: The desired cut position (exclusive)
        fallback_window: How far back to look for a word boundary if no
                         sentence terminator is found

    Returns:
        The adjusted position to cut at (inclusive of the sentence terminator,
        or inclusive of the space at a word boundary).
    """
    if target_pos >= len(text):
        return len(text)

    sentence_terminators = {'.', '?', '!'}

    # Search backwards from target_pos for a sentence terminator
    search_start = max(0, target_pos - fallback_window)
    for i in range(target_pos - 1, search_start - 1, -1):
        if text[i] in sentence_terminators:
            # Include the terminator itself
            return i + 1

    # No sentence terminator found - fall back to last word boundary
    for i in range(target_pos - 1, search_start - 1, -1):
        if text[i] == ' ':
            return i

    # Last resort: hard cut at target_pos
    return target_pos


def extract_youtube_transcript(
    url: str, language: str = "en", offset: int = 0, limit: int = 0,
    timestamps: bool = False,
) -> dict:
    """
    Extract transcript and metadata from a YouTube video URL.

    Downloads from YouTube only on the first call for a given (url, language).
    Subsequent calls within the cache TTL serve from a local cache file so
    pagination does not hammer YouTube's servers.

    Args:
        url: YouTube video URL or ID
        language: Language code for subtitles (default: "en")
        offset: Character offset to start reading from (default: 0)
        limit: Max characters to return. 0 = return full transcript from
               offset (default: 0)
        timestamps: Include [hh:mm:ss] timestamps in the transcript text
                    (default: False)

    Returns:
        Dictionary containing video metadata and transcript:
        - video_id: YouTube video ID
        - title: Video title
        - channel: Channel/uploader name
        - date_posted: Upload date (format: YYYYMMDD)
        - transcript: Full (or chunked) transcript text

        When limit > 0, additional fields are included:
        - transcript_length: Total character count of the full transcript
        - transcript_offset: Suggested offset for the next chunk (aligned to
                             a sentence boundary)
        - transcript_remaining: Characters remaining after this chunk

    Raises:
        ValueError: If URL is invalid
        DownloadError: If transcript cannot be downloaded
    """
    if not url or not isinstance(url, str):
        raise ValueError("Invalid YouTube URL provided")

    cache_file = _cache_dir() / _cache_key(url, language, timestamps)

    # Try cache first
    cached = _load_from_cache(cache_file)
    if cached is not None:
        metadata, full_transcript = cached
    else:
        # Cache miss — download from YouTube
        metadata, full_transcript = _download_transcript(url, language, timestamps)
        _save_to_cache(cache_file, metadata, full_transcript)

    transcript_length = len(full_transcript)

    # Apply offset and limit with sentence-boundary alignment
    if limit > 0 and offset < transcript_length:
        raw_end = min(offset + limit, transcript_length)
        adjusted_end = find_sentence_boundary(full_transcript, raw_end)
        chunk = full_transcript[offset:adjusted_end]
        result = {
            **metadata,
            "transcript": chunk,
            "transcript_length": transcript_length,
            "transcript_offset": adjusted_end,
            "transcript_remaining": max(0, transcript_length - adjusted_end),
        }
    else:
        # No chunking: return full transcript from offset (backward-compatible)
        if offset > 0:
            result = {
                **metadata,
                "transcript": full_transcript[offset:],
                "transcript_length": transcript_length,
                "transcript_offset": transcript_length,
                "transcript_remaining": 0,
            }
        else:
            result = {**metadata, "transcript": full_transcript}

    return result


def _format_srt_timestamp(td) -> str:
    """Format a timedelta as hh:mm:ss."""
    total_secs = int(td.total_seconds())
    hours, remainder = divmod(total_secs, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _download_transcript(url: str, language: str, timestamps: bool = False) -> tuple[dict, str]:
    """
    Download transcript and metadata from YouTube (no caching).

    This is the slow path — called only on cache miss.

    First attempts extraction with the configured options (cookies from
    browser, JS runtime). If that yields no subtitles — which happens for
    some videos because YouTube's web client requires a PO token for
    subtitle downloads when cookies are used — the download is retried
    once with bare default options (no cookies), which uses player
    clients that do not require a PO token.

    Args:
        url: YouTube video URL or ID
        language: Language code for subtitles
        timestamps: If True, prepend [hh:mm:ss] timestamps to each subtitle

    Returns:
        (metadata, full_transcript) tuple.

    Raises:
        DownloadError: If transcript cannot be downloaded.
    """
    last_error: Exception | None = None
    # Wait for the POT HTTP server to become ready before the first
    # cookie-based attempt, so a cold node start does not race extraction.
    _wait_for_pot_server()
    for use_config in (True, False):
        try:
            return _download_transcript_attempt(url, language, timestamps, use_config)
        except DownloadError as e:
            last_error = e
    assert last_error is not None
    raise last_error


def _download_transcript_attempt(
    url: str, language: str, timestamps: bool, use_config: bool,
) -> tuple[dict, str]:
    """
    Perform a single subtitle download attempt.

    Args:
        url: YouTube video URL or ID
        language: Language code for subtitles
        timestamps: If True, prepend [hh:mm:ss] timestamps to each subtitle
        use_config: If True, apply cookies/JS-runtime options from the
            server config; if False, use bare yt-dlp defaults.

    Returns:
        (metadata, full_transcript) tuple.

    Raises:
        DownloadError: If no subtitles could be downloaded.
    """
    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        opts = deepcopy(default_opts)
        opts["outtmpl"] = {"default": str(path / "res")}
        opts["subtitleslangs"] = [language]
        if use_config:
            _apply_ytdlp_opts(opts, _ytdlp_config)

        # Extract metadata first
        ydl = YoutubeDL(opts)
        info = ydl.extract_info(url, download=False)

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
        if timestamps:
            full_transcript = " ".join([
                f"[{_format_srt_timestamp(s.start)}] {s.content.replace(chr(10), ' ')}"
                for s in subtitles
            ])
        else:
            full_transcript = " ".join([s.content.replace("\n", " ") for s in subtitles])

        return metadata, full_transcript


# Create MCP server
mcp = FastMCP("youtube-transcript-mcp")


# Add transcribe tool
@mcp.tool()
async def transcribe(
    url: str,
    language: str = "en",
    offset: int = 0,
    limit: int = 0,
    timestamps: bool = False,
) -> dict | list[TextContent]:
    """
    Extract transcript and metadata from a YouTube video.
    
    IMPORTANT: Parameters must be passed as a JSON object, not named parameters.
    Example: {"url": "https://www.youtube.com/watch?v=VIDEO_ID", "language": "en"}

    DEFAULT BEHAVIOR (limit=0): Returns the full transcript as multiple
    content items in a single tool call. The first item is a JSON dict
    with video metadata (video_id, title, channel, date_posted,
    transcript_length). Subsequent items are plain-text transcript
    chunks aligned to sentence boundaries. You receive everything at
    once — no pagination required.

    PAGINATED BEHAVIOR (limit > 0): Returns a single JSON dict with
    one transcript chunk and pagination fields (transcript_offset,
    transcript_remaining). Use this if you need fine-grained control
    over chunk size.

    The transcript is cached locally after the first download, so
    repeated calls for the same video do NOT re-download from YouTube.
    Call flush_cache() when you are finished to free disk space.

    Args (as JSON object):
        url (string, required): YouTube video URL (e.g., https://www.youtube.com/watch?v=VIDEO_ID)
        language (string, optional): Language code for subtitles. Default: "en"
        offset (integer, optional): Character offset to start reading from. Default: 0. Only used when limit > 0.
        limit (integer, optional): Max characters per chunk when paginating. Default: 0 (returns full transcript as multiple content items). Set > 0 for single-chunk JSON response with pagination fields.
        timestamps (boolean, optional): Include [hh:mm:ss] timestamps in the transcript text. Default: false.

    Returns:
        When limit == 0 (default):
            Multiple content items in one call:
            - Item 1: JSON with video_id, title, channel, date_posted,
                      transcript_length
            - Item 2+: Plain-text transcript chunks (~50K chars each,
                       aligned to sentence boundaries)

        When limit > 0:
            Single JSON dict with:
            - video_id, title, channel, date_posted
            - transcript: Chunk of transcript text
            - transcript_length, transcript_offset, transcript_remaining

    Example (default — full transcript, no pagination):
        transcribe("https://youtube.com/watch?v=VIDEO_ID")
        Returns 3 content items:
          Item 1: {"video_id": "...", "title": "...", "channel": "...",
                   "date_posted": "...", "transcript_length": 45000}
          Item 2: "Today's number 40. That's the percentage of Americans..."
          Item 3: "As Yoda said, there's do or do not..."

    Example (paginated — fine-grained control):
        transcribe("https://youtube.com/watch?v=VIDEO_ID", limit=1900)
        Returns: {"video_id": "...", "transcript": "Today's number 40...",
                  "transcript_length": 45000, "transcript_offset": 1893,
                  "transcript_remaining": 43107}
    """
    try:
        # When limit > 0, use the original dict-based paginated response
        if limit > 0:
            return extract_youtube_transcript(url, language, offset, limit, timestamps)

        # Default: download/cache, then return multiple TextContent items
        cache_file = _cache_dir() / _cache_key(url, language, timestamps)
        cached = _load_from_cache(cache_file)
        if cached is not None:
            metadata, full_transcript = cached
        else:
            metadata, full_transcript = _download_transcript(url, language, timestamps)
            _save_to_cache(cache_file, metadata, full_transcript)

        return _split_into_text_content(metadata, full_transcript)

    except DownloadError as e:
        raise ValueError(f"Failed to download transcript: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error extracting transcript: {str(e)}")


@mcp.tool()
async def flush_cache() -> dict:
    """
    Delete all cached transcript files from the local cache.

    Call this when you are finished reading a transcript to free disk space.
    Cache files also expire automatically after 1 hour and are cleaned up on
    server startup.

    Returns:
        Dictionary with a "deleted" count indicating how many files were removed.
    """
    deleted = _flush_cache()
    return {"deleted": deleted}


def update_yt_dlp() -> None:
    """
    Update yt-dlp to the latest version on server startup (optional).
    
    This function is only called if the MCP_AUTO_UPDATE_YT_DLP environment
    variable is set to 'true' or '1'. By default, auto-update is disabled
    to prevent initialization timeouts.
    
    Manual updates can be performed by running:
    pip install -U --pre yt-dlp[default]
    
    This ensures compatibility with YouTube's frequent changes when needed.
    """
    # Only auto-update if explicitly enabled via environment variable
    auto_update = os.getenv("MCP_AUTO_UPDATE_YT_DLP", "").lower() in ("true", "1", "yes")
    
    if not auto_update:
        print("Skipping yt-dlp auto-update (set MCP_AUTO_UPDATE_YT_DLP=true to enable)", file=sys.stderr)
        return
    
    try:
        print("Updating yt-dlp to the latest version...", file=sys.stderr)
        # Run update with a timeout to prevent hanging
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60  # 60 second timeout for the update process
        )
        print("yt-dlp updated successfully.", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Warning: yt-dlp update timed out, continuing with current version", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to update yt-dlp: {e}", file=sys.stderr)
        if e.stderr:
            print(f"stderr: {e.stderr}", file=sys.stderr)
        # Continue anyway - the server may still work with the existing version


def main():
    """Run MCP server"""
    global _pot_server_thread

    # Update yt-dlp to latest version on startup
    update_yt_dlp()
    
    # Clean stale cache files on startup in case a previous agent did not
    # call flush_cache or crashed mid-session.
    clean_cache()

    # Start (or reuse) the bgutil PO token HTTP server in the background.
    # The first transcript request waits for it via _wait_for_pot_server().
    _pot_server_thread = threading.Thread(
        target=_ensure_pot_server_started, daemon=True)
    _pot_server_thread.start()

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
