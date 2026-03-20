#!/usr/bin/env python3
"""Simple HTTP server for the Content Generator web UI."""

import http.server
import os
import sys

PORT = int(os.environ.get("WEB_PORT", 9503))


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    handler = http.server.SimpleHTTPRequestHandler
    with http.server.HTTPServer(("0.0.0.0", PORT), handler) as httpd:
        print(f"Content Generator UI → http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
