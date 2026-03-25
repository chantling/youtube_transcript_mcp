#!/usr/bin/env python3
"""
Wrapper script to run youtube_transcript_mcp server.
This can be called directly without package installation.
"""

import sys
import os

# Add the package directory to Python path
package_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, package_dir)

# Import and run the main function
if __name__ == "__main__":
    from youtube_transcript_mcp import main
    main()
