#!/usr/bin/env python3
"""Run the AdCP Sales Agent with HTTP transport.

Boots one of two stacks based on ``RUN_STACK``:

* ``core`` (default on this fork): ``core.main.main()`` — adcp.server.serve()
  pattern, MCP at /mcp, A2A at /, Flask admin via WSGI middleware. Single
  binary, single event loop. This is the fork's reason for existing.
* ``src``: ``uvicorn.run("src.app:app", ...)`` — legacy FastAPI assembly
  with the disabled-on-1.0+ a2a_server. Escape hatch if core/ regresses.
"""

import os
import sys


def main():
    """Run the server with configurable port + stack."""
    # Initialize application with startup validation. Both stacks share the
    # same DB schema + startup checks, so we run this regardless.
    try:
        sys.path.insert(0, ".")
        from src.core.startup import initialize_application

        print("Initializing AdCP Sales Agent...")
        initialize_application()
        print("Application initialization completed")

    except SystemExit:
        print("Application initialization failed - check logs")
        sys.exit(1)
    except Exception as e:
        print(f"Startup error: {e}")
        sys.exit(1)

    port = int(os.environ.get("ADCP_SALES_PORT", "8080"))
    host = os.environ.get("ADCP_SALES_HOST", "0.0.0.0")
    if os.environ.get("FLY_APP_NAME") or os.environ.get("PRODUCTION"):
        host = "0.0.0.0"

    stack = os.environ.get("RUN_STACK", "core").lower()
    print(f"Starting AdCP Sales Agent on {host}:{port} (stack={stack})")
    print(f"Server endpoint: http://{host}:{port}/")

    if stack == "src":
        # Legacy stack — kept reachable as an escape hatch while core/
        # bakes in production. Remove once core/ has run a full release
        # cycle without regression.
        import uvicorn

        try:
            uvicorn.run("src.app:app", host=host, port=port, log_level="info")
        except KeyboardInterrupt:
            print("\nServer stopped.")
            sys.exit(0)
        return

    if stack != "core":
        print(f"Unknown RUN_STACK={stack!r}; expected 'core' or 'src'")
        sys.exit(2)

    # Default path on this fork: core.main owns the server.
    os.environ.setdefault("ADCP_PORT", str(port))
    from core.main import main as _core_main

    try:
        _core_main()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
