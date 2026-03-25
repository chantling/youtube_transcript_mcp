"""YouTube Transcript MCP Package Initialization"""

# Import from server module where everything is defined
from .server import (
    extract_youtube_transcript,
    main,
    mcp,
    transcribe,
)

# Export components
__all__ = [
    "extract_youtube_transcript",
    "main",
    "mcp",
    "transcribe",
]