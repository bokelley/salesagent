"""Anonymize captured FreeWheel fixtures for safe OSS commit.

Reads:  .context/freewheel-fixtures/{v3,v4}/...
Writes: tests/fixtures/data/freewheel/{v3,v4}/...

Anonymization rules:
- IDs (numeric): preserved verbatim. They are not PII and we need them for
  referential integrity across fixtures and replay tests.
- Sensitive text fields (name, description, external_id, PII fields, etc.):
  deterministically replaced with structured fakes. Same original value
  → same fake everywhere (so a campaign name appearing in two files stays
  consistent). Empty values stay empty.
- Structural fields (status, stage, dates, currency, budget, schedule,
  link hrefs, pagination): preserved verbatim.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW = REPO_ROOT / ".context" / "freewheel-fixtures"
OUT = REPO_ROOT / "tests" / "fixtures" / "data" / "freewheel"

# Per-field replacement strategies. Maps field name -> template using a stable
# counter for that field. Same original value reused across files maps to the
# same fake.
SENSITIVE_FIELDS: dict[str, str] = {
    # Common — generic naming because the same value can appear across
    # multiple resource types (e.g. a site name reused as a section name)
    # and a {type} placeholder would resolve from whichever file hit first.
    "name": "name {n}",
    "description": "description {n}",
    "external_id": "EXT-{n:08d}",
    # v3 commercial PII
    "client_po": "PO-{n}",
    "brand_id": "BRAND-{n}",
    "primary_sales_person": "Person {n}",
    "primary_trafficker": "Person {n}",
    "billing_term": "TERMS_{n}",
    "instruction": "instruction {n}",
    # v4 inventory: content titles + credits
    "tag": "tag_{n}",
    "metadata": "metadata_{n}",
    "url": "https://example.invalid/{n}",
    # Creative rendition VAST tag / asset URI (third-party ad-server hostname
    # would otherwise leak the publisher's exchange relationships).
    "uri": "https://example.invalid/vast/{n}",
    "clearcast_note": "clearcast_note {n}",
    "title1": "title1 {n}",
    "title2": "title2 {n}",
    "actor": "Actor {n}",
    "director": "Director {n}",
    "producer": "Producer {n}",
    "writer": "Writer {n}",
    "vod_metadata": "vod_metadata {n}",
}

# Integer fields whose value identifies a specific entity that was named in
# publisher-supplied context (network = the publisher itself, advertiser =
# the named "Test Account Advertiser"). Replacements are deterministic and
# consistent across files so foreign-key joins still work in fixtures.
# Per-entity IDs for sites/sections/series/videos/etc. are intentionally
# preserved — they're meaningless integers without the network scope.
SENSITIVE_INT_FIELDS: set[str] = {
    "network_id",
    "advertiser_id",
    "agency_id",
    "upstream_asset_id",
    "upstream_network_id",
    "content_owner_id",
}

# List-of-X fields where every element is sensitive (each gets scrubbed
# through the corresponding scalar machinery).
SENSITIVE_LIST_FIELDS: dict[str, tuple[str, str]] = {
    # field -> (scrub_type, scrub_key); type is "str" or "int"
    "secondary_ids": ("str", "external_id"),
    "content_partner_ids": ("int", "content_owner_id"),
    # Creative ↔ advertiser linkage; each id maps through the same memo
    # as the scalar advertiser_id so cross-references stay consistent.
    "advertiser_ids": ("int", "advertiser_id"),
    "agency_ids": ("int", "agency_id"),
}

# In v3 XML, an advertiser's primary key uses the generic <id> tag. Treat <id>
# as advertiser_id when its parent element is an advertiser record.
XML_ADVERTISER_PARENTS: set[str] = {"advertiser"}
_INT_FAKE_BASE = 100000
_int_memo: dict[tuple[str, int], int] = {}
_int_counters: dict[str, int] = {}


def fake_int_for(field: str, original: int) -> int:
    key = (field, original)
    if key in _int_memo:
        return _int_memo[key]
    _int_counters[field] = _int_counters.get(field, 0) + 1
    fake = _INT_FAKE_BASE + _int_counters[field]
    _int_memo[key] = fake
    return fake


# Memoized mapping: (field, original_value) -> fake_value. Ensures same input
# gives same output across all fixture files in a single run.
_memo: dict[tuple[str, str], str] = {}
_counters: dict[str, int] = {}


def fake_for(field: str, original: str) -> str:
    """Deterministic fake for `original` keyed by `field`.

    Memoized globally so the same input string maps to the same fake everywhere
    (preserves referential integrity across fixture files).
    """
    if original == "" or original is None:
        return original
    key = (field, original)
    if key in _memo:
        return _memo[key]
    _counters[field] = _counters.get(field, 0) + 1
    n = _counters[field]
    fake = SENSITIVE_FIELDS[field].format(n=n)
    _memo[key] = fake
    return fake


# ---------- JSON (v4) ----------


def anonymize_json(data: object) -> object:
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k in SENSITIVE_FIELDS and isinstance(v, str):
                result[k] = fake_for(k, v)
            elif k in SENSITIVE_INT_FIELDS and isinstance(v, int):
                result[k] = fake_int_for(k, v)
            elif k in SENSITIVE_LIST_FIELDS and isinstance(v, list):
                scrub_type, scrub_key = SENSITIVE_LIST_FIELDS[k]
                if scrub_type == "str":
                    result[k] = [fake_for(scrub_key, str(x)) if x else x for x in v]
                else:
                    result[k] = [fake_int_for(scrub_key, int(x)) if x is not None else x for x in v]
            elif k == "customized_metadata" and isinstance(v, dict) and v:
                result[k] = {ck: fake_for("metadata", str(cv)) for ck, cv in v.items()}
            else:
                result[k] = anonymize_json(v)
        return result
    if isinstance(data, list):
        return [anonymize_json(x) for x in data]
    return data


def process_json_file(src: Path, dst: Path) -> None:
    raw = json.loads(src.read_text())
    cleaned = anonymize_json(raw)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(cleaned, indent=2, sort_keys=True) + "\n")


# ---------- XML (v3) ----------


def anonymize_xml(elem: ET.Element, parent_tag: str = "") -> None:
    for child in elem:
        anonymize_xml(child, parent_tag=elem.tag)
        tag = child.tag
        text = child.text
        if not text or not text.strip():
            continue
        if tag in SENSITIVE_FIELDS:
            child.text = fake_for(tag, text)
        elif tag in SENSITIVE_INT_FIELDS:
            try:
                child.text = str(fake_int_for(tag, int(text)))
            except ValueError:
                pass
        elif tag == "id" and elem.tag in XML_ADVERTISER_PARENTS:
            try:
                child.text = str(fake_int_for("advertiser_id", int(text)))
            except ValueError:
                pass


def process_xml_file(src: Path, dst: Path) -> None:
    tree = ET.parse(src)
    root = tree.getroot()
    anonymize_xml(root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    body = ET.tostring(root, encoding="unicode")
    dst.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n")


# ---------- Driver ----------


def main() -> None:
    if not RAW.exists():
        sys.exit(f"No raw captures at {RAW}; run capture_freewheel_fixtures.py first")

    if OUT.exists():
        for p in sorted(OUT.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
    OUT.mkdir(parents=True, exist_ok=True)

    token_info_src = RAW / "_token_info.json"
    if token_info_src.exists():
        info = json.loads(token_info_src.read_text())
        # Replace user_id with a stable fake to avoid linking the fixture to
        # a real account.
        info["user_id"] = 0
        (OUT / "_token_info.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")

    count = 0
    for src in sorted(RAW.rglob("*")):
        if not src.is_file() or src.name == "_token_info.json":
            continue
        rel = src.relative_to(RAW)
        dst = OUT / rel
        parts = rel.parts
        version = parts[0]
        if version == "v4" and src.suffix == ".json":
            process_json_file(src, dst)
            count += 1
        elif version == "v3" and src.suffix == ".xml":
            process_xml_file(src, dst)
            count += 1
        else:
            print(f"  skip: {rel}")

    print(f"Anonymized {count} files -> {OUT}")
    print(f"Memoized replacements: {len(_memo)}")


if __name__ == "__main__":
    main()
