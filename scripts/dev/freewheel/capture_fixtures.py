"""Capture FreeWheel API responses to local files for fixture use.

Walks two surfaces:
  - /services/v4/* (JSON, inventory taxonomy)
  - /services/v3/* (XML,  commercial entities)

Output: .context/freewheel-fixtures/{v3,v4}/<resource>/<shape>.{json,xml}

Token: read from .env via FREEWHEEL_TEST_API_KEY.

Per readable endpoint we save:
  - list_page1            (per_page=10)
  - list_page2            (if total_pages >= 2)
  - single                (first item by id)
  - linked_<rel>          (each `rel` link on the first v4 item, except `self`)
  - filtered_by_test_adv  (v3 only, for resources that accept advertiser_id filter)
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE = "https://api.freewheel.tv"
REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / ".context" / "freewheel-fixtures"
# Test advertiser ID is publisher-specific; load from env to keep this script
# free of identifying constants.
TEST_ADVERTISER_ID_ENV = "FREEWHEEL_TEST_ADVERTISER_ID"

V4_RESOURCES = [
    "sites",
    "site_sections",
    "site_groups",
    "series",
    "videos",
    "video_groups",
    "inventory_packages",
    "creative_resources",
]

V3_RESOURCES = [
    "advertisers",
    "campaigns",
    "insertion_orders",
    "placements",
    "agencies",
]

V3_ADVERTISER_SCOPED = {"campaigns", "insertion_orders", "placements"}


def load_env(name: str) -> str:
    env_path = REPO_ROOT / ".env"
    prefix = f"{name}="
    for line in env_path.read_text().splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} not found in .env")


def request(session: requests.Session, path: str, **params) -> tuple[int, str]:
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    accept = "application/xml" if "/services/v3/" in path else "application/json"
    r = session.get(url, headers={"accept": accept}, timeout=30)
    return r.status_code, r.text


def save(version: str, resource: str, shape: str, body: str, ext: str) -> None:
    d = OUT / version / resource
    d.mkdir(parents=True, exist_ok=True)
    if ext == "json":
        d.joinpath(f"{shape}.json").write_text(json.dumps(json.loads(body), indent=2, sort_keys=True))
    else:
        d.joinpath(f"{shape}.{ext}").write_text(body)


def first_item_id_v4(body: str) -> int | None:
    data = json.loads(body)
    items = data.get("items") or []
    if items and "id" in items[0]:
        return items[0]["id"]
    return None


def linked_rels_v4(body: str) -> list[tuple[str, str]]:
    data = json.loads(body)
    items = data.get("items") or []
    if not items:
        return []
    out = []
    for link in items[0].get("links") or []:
        rel, href = link.get("rel"), link.get("href")
        if rel and rel != "self" and href:
            out.append((rel, href))
    return out


def first_item_id_v3(body: str, resource: str) -> int | None:
    # XML root is the plural collection; child elements are singular.
    singular = resource.rstrip("s") if resource.endswith("s") else resource
    # Handle "agencies" → "agency", "categories" → "category" if it ever comes up.
    if resource == "agencies":
        singular = "agency"
    elif resource == "categories":
        singular = "category"
    elif resource == "insertion_orders":
        singular = "insertion_order"
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    first = root.find(singular)
    if first is None:
        return None
    id_el = first.find("id")
    if id_el is None or id_el.text is None:
        return None
    return int(id_el.text)


def total_pages_v3(body: str) -> int:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return 1
    return int(root.attrib.get("total_pages", "1"))


def total_pages_v4(body: str) -> int:
    data = json.loads(body)
    return int(data.get("total_page", data.get("total_pages", 1)))


def capture_v4(session: requests.Session, resource: str) -> None:
    print(f"\n[v4] {resource}")
    path = f"/services/v4/{resource}"

    code, body = request(session, path, per_page=10)
    print(f"  GET {path}?per_page=10 -> {code}")
    if code != 200:
        return
    save("v4", resource, "list_page1", body, "json")

    if total_pages_v4(body) >= 2:
        code, body2 = request(session, path, per_page=10, page=2)
        print(f"  GET {path}?page=2 -> {code}")
        if code == 200:
            save("v4", resource, "list_page2", body2, "json")

    item_id = first_item_id_v4(body)
    if item_id is None:
        return

    code, single = request(session, f"{path}/{item_id}")
    print(f"  GET {path}/{item_id} -> {code}")
    if code == 200:
        save("v4", resource, "single", single, "json")

    for rel, href in linked_rels_v4(body):
        code, linked = request(session, href, per_page=5)
        print(f"  GET {href} ({rel}) -> {code}")
        if code == 200:
            save("v4", resource, f"linked_{rel}", linked, "json")


def capture_v3(session: requests.Session, resource: str, test_advertiser_id: int) -> None:
    print(f"\n[v3] {resource}")
    path = f"/services/v3/{resource}"

    code, body = request(session, path, per_page=10)
    print(f"  GET {path}?per_page=10 -> {code}")
    if code != 200:
        return
    save("v3", resource, "list_page1", body, "xml")

    if total_pages_v3(body) >= 2:
        code, body2 = request(session, path, per_page=10, page=2)
        print(f"  GET {path}?page=2 -> {code}")
        if code == 200:
            save("v3", resource, "list_page2", body2, "xml")

    item_id = first_item_id_v3(body, resource)
    if item_id is not None:
        code, single = request(session, f"{path}/{item_id}")
        print(f"  GET {path}/{item_id} -> {code}")
        if code == 200:
            save("v3", resource, "single", single, "xml")

    if resource in V3_ADVERTISER_SCOPED:
        code, filtered = request(session, path, advertiser_id=test_advertiser_id, per_page=10)
        print(f"  GET {path}?advertiser_id=<test> -> {code}")
        if code == 200:
            save("v3", resource, "filtered_by_test_advertiser", filtered, "xml")
            scoped_item_id = first_item_id_v3(filtered, resource)
            if scoped_item_id is not None and scoped_item_id != item_id:
                code, scoped_single = request(session, f"{path}/{scoped_item_id}")
                print(f"  GET {path}/{scoped_item_id} -> {code}")
                if code == 200:
                    save("v3", resource, "single_test_advertiser", scoped_single, "xml")

    if resource == "advertisers":
        code, adv = request(session, f"{path}/{test_advertiser_id}")
        print(f"  GET {path}/<test_advertiser> -> {code}")
        if code == 200:
            save("v3", resource, "test_account_advertiser", adv, "xml")


def main() -> None:
    token = load_env("FREEWHEEL_TEST_API_KEY")
    test_advertiser_id = int(load_env(TEST_ADVERTISER_ID_ENV))
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    OUT.mkdir(parents=True, exist_ok=True)

    code, info = request(session, "/auth/token/info")
    print(f"GET /auth/token/info -> {code}: {info[:200]}")
    if code == 200:
        (OUT / "_token_info.json").write_text(json.dumps(json.loads(info), indent=2, sort_keys=True))

    for resource in V4_RESOURCES:
        capture_v4(session, resource)

    for resource in V3_RESOURCES:
        capture_v3(session, resource, test_advertiser_id)

    print(f"\nDone. Raw fixtures in {OUT}")


if __name__ == "__main__":
    main()
