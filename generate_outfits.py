import contextlib
import json
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

import requests

NPC_OUTFITTER_PAGE = "NPC Outfitter Codes"
MW_API_URL = "https://tibia.fandom.com/api.php"
OUTPUT_JSON = "outfits.json"
DB_PATH = "tibiawiki.db"
OUTFITS_XML = "outfits.xml"


def normalize_name(name: str) -> str:
    return name.strip()


def alt_name_without_npc_suffix(name: str) -> str:
    """
    'Akananto (NPC)' -> 'Akananto'
    """
    return re.sub(r"\s*\(NPC\)\s*$", "", name).strip()


def normalize_gender(flag: Optional[str]) -> Optional[str]:
    if not flag:
        return None
    flag = flag.strip().lower()
    if flag.startswith("f"):
        return "female"
    if flag.startswith("m"):
        return "male"
    return None


def is_truthy_flag(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"true", "yes", "1", "y"}


def parse_outfiter_url(url: str) -> Dict[str, Any]:
    """
    Parse an Outfiter URL and return a dict with outfit info:
    {type, head, body, legs, feet, addons, sex}
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    def get_int(key: str) -> Optional[int]:
        if key not in qs:
            return None
        val = qs[key][0]
        if not val:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    outfit_type = get_int("o")  # outfit lookType id
    c1 = get_int("c1")  # head
    c2 = get_int("c2")  # body
    c3 = get_int("c3")  # legs
    c4 = get_int("c4")  # feet

    # Addons: presence of a1/a2 query flags
    addons = 0
    if "a1" in qs:
        addons |= 1
    if "a2" in qs:
        addons |= 2

    sex = None
    # Some links contain "fm" (female) or "f" values
    if "fm" in qs:
        sex = "female"
    elif "f" in qs:
        sex = normalize_gender(qs["f"][0])

    result: Dict[str, Any] = {}
    if outfit_type is not None:
        result["type"] = outfit_type
    if c1 is not None:
        result["head"] = c1
    if c2 is not None:
        result["body"] = c2
    if c3 is not None:
        result["legs"] = c3
    if c4 is not None:
        result["feet"] = c4
    result["addons"] = addons  # always present, 0-3
    if sex:
        result["sex"] = sex

    return result


def fetch_page_wikitext(title: str) -> str:
    resp = requests.get(
        MW_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvslots": "main",
            "rvprop": "content",
            "titles": title,
        },
        headers={"User-Agent": "Mozilla/5.0 (NPC outfit scraper)"},
    )
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {}) or {}
    for page in pages.values():
        revs = page.get("revisions") or []
        if not revs:
            continue
        slot = revs[0]
        if "slots" in slot:
            return slot["slots"]["main"]["*"]
        if "*" in slot:
            return slot["*"]
    return ""


def parse_outfitter_template(template: str) -> Dict[str, str]:
    """
    Parse a simple {{Outfitter|...}} template string into a dict.
    """
    template = template.strip()
    if template.startswith("{{"):
        template = template[2:]
    if template.endswith("}}"):
        template = template[:-2]
    parts = template.split("|")
    params: Dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip()] = value.strip()
        elif part.strip():
            params[part.strip()] = "true"
    return params


def parse_wikitext_rows(wikitext: str):
    """
    Yield tuples of (name, link, template_str) from the wikitable in the page.
    The export keeps each row on a single line after a "|-" separator, so we
    parse line-by-line to avoid capturing separators as part of the name.
    """
    for line in wikitext.splitlines():
        line = line.strip()
        if not line.startswith("| "):
            continue
        # drop leading "| "
        row = line[2:]
        parts = [p.strip() for p in row.split("||")]
        if len(parts) < 3:
            continue
        name, link, template_str = parts[:3]
        if not template_str.startswith("{{"):
            continue
        yield normalize_name(name), link, template_str


def build_looktype_map() -> Dict[int, Dict[str, Any]]:
    """
    Build a mapping of looktype -> {outfit_id, outfit_name, sex}
    using the outfits stored in tibiawiki.db and fetching their infobox data.
    """
    looktype_map: Dict[int, Dict[str, Any]] = {}
    if not os.path.exists(DB_PATH):
        return looktype_map

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT article_id, title, name FROM outfit")
    outfits = cur.fetchall()
    conn.close()

    # Request in batches to reduce round-trips
    batch_size = 20
    for i in range(0, len(outfits), batch_size):
        batch = outfits[i : i + batch_size]
        titles = "|".join(title for _, title, _ in batch)
        resp = requests.get(
            MW_API_URL,
            params={
                "action": "query",
                "format": "json",
                "prop": "revisions",
                "rvslots": "main",
                "rvprop": "content",
                "titles": titles,
            },
            headers={"User-Agent": "Mozilla/5.0 (NPC outfit scraper)"},
        )
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {}) or {}
        title_to_content = {
            page.get("title"): (
                page.get("revisions", [{}])[0]
                .get("slots", {})
                .get("main", {})
                .get("*")
                or page.get("revisions", [{}])[0].get("*", "")
            )
            for page in pages.values()
        }

        for article_id, title, name in batch:
            content = title_to_content.get(title) or ""
            male_match = re.search(r"male_id\s*=\s*([0-9]+)", content)
            female_match = re.search(r"female_id\s*=\s*([0-9]+)", content)
            if male_match:
                looktype_map[int(male_match.group(1))] = {
                    "outfit_id": article_id,
                    "outfit_name": name,
                    "sex": "male",
                }
            if female_match:
                looktype_map[int(female_match.group(1))] = {
                    "outfit_id": article_id,
                    "outfit_name": name,
                    "sex": "female",
                }
    return looktype_map


def load_outfits_xml_lookup(path: str) -> tuple[Dict[int, Dict[str, Any]], Dict[str, list[tuple[int, Dict[str, Any]]]]]:
    """
    Build:
      - mapping of looktype -> {outfit_name, sex}
      - ordered list of (looktype, info) per sex, keeping file order.
    This enables both direct looktype matches and an index-based fallback.
    """
    mapping: Dict[int, Dict[str, Any]] = {}
    ordered: Dict[str, list[tuple[int, Dict[str, Any]]]] = {"female": [], "male": []}
    if not os.path.exists(path):
        return mapping, ordered

    tree = ET.parse(path)
    root = tree.getroot()
    for outfit in root.findall("outfit"):
        looktype = outfit.attrib.get("looktype")
        name = outfit.attrib.get("name")
        sex_type = outfit.attrib.get("type")
        if not name or looktype is None or sex_type is None:
            continue
        sex = "female" if sex_type == "0" else "male"
        try:
            lt_int = int(looktype)
        except ValueError:
            continue
        info = {"outfit_name": name.strip(), "sex": sex}
        mapping[lt_int] = info
        ordered.setdefault(sex, []).append((lt_int, info))
    return mapping, ordered


def main():
    print(f"Downloading NPC Outfitter Codes from page '{NPC_OUTFITTER_PAGE}' ...")
    wikitext = fetch_page_wikitext(NPC_OUTFITTER_PAGE)
    looktype_map = build_looktype_map()
    outfits_xml_lookup, outfits_xml_order = load_outfits_xml_lookup(OUTFITS_XML)
    outfits = {}

    for npc_name, href, template_str in parse_wikitext_rows(wikitext):
        if not href:
            continue

        # Normalize href to absolute URL
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://tibia.fandom.com" + href

        outfit_info = parse_outfiter_url(href)

        template_params = parse_outfitter_template(template_str)

        # Fallback outfit id/type from template if missing in the link
        template_outfit = template_params.get("outfit")
        if template_outfit and "type" not in outfit_info:
            with contextlib.suppress(ValueError):
                outfit_info["type"] = int(template_outfit)

        # Addons may be encoded only in the template
        if "addons" not in outfit_info:
            outfit_info["addons"] = 0
        if "addon1" in template_params:
            if is_truthy_flag(template_params["addon1"]) or template_params["addon1"] == "":
                outfit_info["addons"] |= 1
        if "addon2" in template_params:
            if is_truthy_flag(template_params["addon2"]) or template_params["addon2"] == "":
                outfit_info["addons"] |= 2

        # Sex: prefer explicit template flag, otherwise keep link guess
        if "sex" not in outfit_info:
            sex = None
            if "female" in template_params:
                if is_truthy_flag(template_params["female"]) or template_params["female"] == "":
                    sex = "female"
            sex = sex or normalize_gender(template_params.get("sex"))
            if sex:
                outfit_info["sex"] = sex

        looktype = outfit_info.get("type")
        if looktype is not None:
            lookup = looktype_map.get(looktype)
            if lookup:
                outfit_info["outfit_id"] = lookup["outfit_id"]
                outfit_info["outfit_name"] = lookup["outfit_name"]
                if "sex" not in outfit_info and lookup.get("sex"):
                    outfit_info["sex"] = lookup["sex"]
            else:
                xml_fallback = outfits_xml_lookup.get(looktype, {})
                if xml_fallback:
                    outfit_info["outfit_name"] = xml_fallback.get("outfit_name")
                    if "sex" not in outfit_info and xml_fallback.get("sex"):
                        outfit_info["sex"] = xml_fallback["sex"]
                    outfit_info["outfit_id"] = looktype
                else:
                    # Legacy heuristic: small ids on the wiki often represent the
                    # index in outfits.xml for the given sex block.
                    sex_key = outfit_info.get("sex")
                    if not sex_key:
                        # default to female for historical reasons; correct later via NPC gender
                        sex_key = "female"
                    order_list = outfits_xml_order.get(sex_key, [])
                    if isinstance(looktype, int) and 0 <= looktype < len(order_list):
                        mapped_lt, mapped_info = order_list[looktype]
                        outfit_info["type"] = mapped_lt
                        outfit_info["outfit_id"] = mapped_lt
                        outfit_info["outfit_name"] = mapped_info.get("outfit_name")
                        outfit_info["sex"] = sex_key
                    else:
                        # Store at least the looktype id as "outfit_id" to keep the field present
                        outfit_info["outfit_id"] = looktype

        # Main key: as displayed
        outfits[npc_name] = outfit_info

        # Alternate key: without " (NPC)" suffix
        alt = alt_name_without_npc_suffix(npc_name)
        if alt and alt != npc_name:
            outfits.setdefault(alt, outfit_info)

    # Sort keys for nicer JSON
    sorted_outfits = dict(sorted(outfits.items(), key=lambda kv: kv[0].lower()))

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(sorted_outfits, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(sorted_outfits)} entries to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
