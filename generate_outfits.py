import json
import re
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

NPC_OUTFITTER_URL = "https://tibia.fandom.com/wiki/NPC_Outfitter_Codes"
OUTPUT_JSON = "outfits.json"


def normalize_name(name: str) -> str:
    return name.strip()


def alt_name_without_npc_suffix(name: str) -> str:
    """
    'Akananto (NPC)' -> 'Akananto'
    """
    return re.sub(r"\s*\(NPC\)\s*$", "", name).strip()


def parse_outfiter_url(url: str):
    """
    Parse an Outfiter URL and return a dict with outfit info:
    {type, head, body, legs, feet, addons}
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    def get_int(key):
        if key not in qs:
            return None
        val = qs[key][0]
        if not val:
            return None
        try:
            return int(val)
        except ValueError:
            return None

    outfit_type = get_int("o")  # outfit ID / lookType
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

    result = {}
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
    result["addons"] = addons  # always present, 0–3

    return result


def main():
    print(f"Downloading NPC Outfitter Codes from {NPC_OUTFITTER_URL} ...")
    resp = requests.get(
        NPC_OUTFITTER_URL, headers={"User-Agent": "Mozilla/5.0 (NPC outfit scraper)"}
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Article content wrapper
    content = soup.find("div", class_="mw-parser-output") or soup

    outfits = {}

    # Iterate over all tables under the main article content
    for table in content.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            # First column: NPC name
            npc_name = normalize_name(cells[0].get_text(strip=True))
            if not npc_name or npc_name.lower() in {"name", "npc"}:
                # skip header row etc.
                continue

            # Second column: Outfiter link
            link = cells[1].find("a", href=lambda h: h and "Outfiter?" in h)
            if not link:
                continue

            href = link.get("href", "")
            if not href:
                continue

            # Normalize href to absolute URL
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://tibia.fandom.com" + href

            outfit_info = parse_outfiter_url(href)
            if "type" not in outfit_info:
                # no outfit id, skip
                continue

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
