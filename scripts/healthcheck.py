"""Container healthcheck helper using only the Python standard library."""

from __future__ import annotations

import sys
import urllib.request


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "8080"
    urllib.request.urlopen(f"http://localhost:{port}/health", timeout=3).read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
