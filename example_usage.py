#!/usr/bin/env python3
"""
Example usage of the YouTube Transcript MCP Server with metadata.

This example demonstrates how to:
1. Extract transcript and metadata from a YouTube video
2. Parse the JSON response
3. Access individual metadata fields
"""

import asyncio
import json
from mcp.client.stdio import stdio_client
from mcp import Client


async def main():
    """Main example function"""
    print("YouTube Transcript MCP Server - Metadata Example\n")
    print("=" * 50)

    async with stdio_client() as (read_stream, write_stream):
        client = Client("stdio-server", read_stream, write_stream)
        await client.initialize()

        # Example video URL
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

        print(f"\nFetching transcript and metadata for:")
        print(f"URL: {video_url}\n")

        # Call the transcribe tool
        result = await client.call_tool(
            "transcribe", arguments={"url": video_url, "language": "en"}
        )

        # Parse the JSON response
        if hasattr(result, "content"):
            data = json.loads(result.content[0].text)

            # Display metadata
            print("Metadata:")
            print(f"  Video ID: {data['video_id']}")
            print(f"  Title: {data['title']}")
            print(f"  Channel: {data['channel']}")
            print(f"  Date Posted: {data['date_posted']}")

            # Display transcript preview
            transcript = data["transcript"]
            print(f"\nTranscript ({len(transcript)} characters):")
            print("-" * 50)
            print(transcript[:500] + "..." if len(transcript) > 500 else transcript)

            # Example of how to work with the data
            print("\n" + "=" * 50)
            print("Example: Creating a markdown summary")
            print("=" * 50)

            md_content = f"""# {data["title"]}

**Video ID:** {data["video_id"]}  
**Channel:** {data["channel"]}  
**Date Posted:** {data["date_posted"]}  

## Transcript Summary

[Transcript content would be processed and summarized here by an LLM...]

Full transcript follows below:

{transcript}
"""
            print("\nGenerated Markdown Preview:")
            print("-" * 50)
            print(md_content[:600] + "...")

            print("\n" + "=" * 50)
            print("✓ Successfully extracted transcript and metadata!")

        elif hasattr(result, "isError") and result.isError:
            print(f"✗ Error: {result.content}")
        else:
            print(f"✗ Unexpected result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
