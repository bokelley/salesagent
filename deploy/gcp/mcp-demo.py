#!/usr/bin/env -S uv run --quiet --with fastmcp python
"""Smoke-test the deployed MCP endpoint and show the 3.0 protocol shape.

Run from the workspace root:
    .context/gcp-deploy/mcp-demo.py
"""
import asyncio
import json
import os
import sys

from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = os.environ.get("ADCP_URL")
TOKEN = os.environ.get("ADCP_TOKEN")
if not URL or not TOKEN:
    sys.exit("Set ADCP_URL=http://<host>:8000/mcp/ and ADCP_TOKEN=<principal-token>")


async def main() -> None:
    transport = StreamableHttpTransport(url=URL, headers={"x-adcp-auth": TOKEN})
    async with Client(transport=transport) as client:
        print(f"Connected to: {URL}\n")

        tools = await client.list_tools()
        print(f"=== {len(tools)} tools exposed ===")
        for t in tools:
            desc = (t.description or "").split("\n", 1)[0][:80]
            print(f"  • {t.name:35s}  {desc}")

        print("\n=== get_adcp_capabilities (V3 shape) ===")
        try:
            r = await client.call_tool("get_adcp_capabilities", {})
            print(json.dumps(r.data if hasattr(r, "data") else r.structured_content, indent=2)[:2000])
        except Exception as e:
            print(f"(not exposed or errored: {e})")

        print("\n=== get_products (brief='video ads') ===")
        try:
            r = await client.call_tool("get_products", {"brief": "video ads"})
            payload = r.data if hasattr(r, "data") else r.structured_content
            if isinstance(payload, dict) and "products" in payload:
                print(f"Returned {len(payload['products'])} products. First:")
                print(json.dumps(payload["products"][0] if payload["products"] else {}, indent=2)[:1500])
            else:
                print(json.dumps(payload, indent=2)[:1500])
        except Exception as e:
            print(f"(errored: {e})")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
