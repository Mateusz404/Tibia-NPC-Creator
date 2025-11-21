import os
import re
import json
import sqlite3
from typing import Dict, Any, Tuple, List

from tibiawikisql.models.npc import Npc
from tibiawikisql.models.item import Item

# === CONFIG ===

DB_PATH = "tibiawiki.db"

OUTPUT_ROOT = "output"

# Optional outfit mapping file (NPC name -> outfit dict)
OUTFITS_JSON = "outfits.json"

# Default outfit if none is found for an NPC in outfits.json
DEFAULT_OUTFIT = {
    "type": 130,  # mage male
    "head": 19,
    "body": 86,
    "legs": 87,
    "feet": 95,
    "addons": 0,
}


# === HELPERS ===


def slugify_name(name: str) -> str:
    """
    Convert an NPC name to a safe file-base:
    'Simon The Beggar' -> 'simon_the_beggar'
    """
    name = name.strip().lower()
    name = name.replace(" ", "_")
    name = re.sub(r"[^a-z0-9_]+", "", name)
    if not name:
        name = "npc"
    return name


def slugify_city(city: str) -> str:
    """
    Convert a city name to a safe folder name.
    'Thais' -> 'thais'; 'Kazordoon Mines' -> 'kazordoon_mines'
    """
    if not city:
        return "unknown"
    city = city.strip().lower()
    city = city.replace(" ", "_")
    city = re.sub(r"[^a-z0-9_]+", "", city)
    if not city:
        city = "unknown"
    return city


def xml_escape_attr(s: str) -> str:
    """
    Escape characters that are problematic in XML attribute values.
    """
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def load_outfits(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load outfits.json if it exists; otherwise return empty mapping.
    Keys are NPC names as they appear in TibiaWiki (e.g. 'Xodet').
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # normalize keys to lowercase for case-insensitive lookup
    return {k.lower(): v for k, v in data.items()}


def get_outfit_for_npc(
    npc_name: str, outfits: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Return outfit dict for the NPC:
    - if present in outfits.json (case-insensitive), use that
    - otherwise use DEFAULT_OUTFIT
    """
    key = npc_name.lower()
    if key in outfits:
        outfit = outfits[key]
        # ensure all needed keys are present; fill missing from default
        result = DEFAULT_OUTFIT.copy()
        result.update(outfit)
        return result
    return DEFAULT_OUTFIT


def build_shop_value(entries: List[Tuple[str, int, int]]) -> str:
    """
    Build the string for shop_buyable / shop_sellable value attribute.
    entries: list of (name, client_id, price)
    Produces:
      "\\n\\t\\t\\titem1,1111,10;\\n\\t\\t\\titem2,2222,20;"
    """
    if not entries:
        return ""

    lines = []
    for name, client_id, price in entries:
        # Do minimal XML escaping in the item name
        escaped_name = xml_escape_attr(name)
        lines.append(f"\t\t\t{escaped_name},{client_id},{price};")

    return "\n" + "\n".join(lines)


def ensure_city_dirs(city_slug: str) -> Tuple[str, str, str]:
    """
    Make sure the folders for a given city exist and return:
    (npc_dir, scripts_dir, shops_dir)
    """
    npc_dir = os.path.join(OUTPUT_ROOT, city_slug)
    scripts_dir = os.path.join(npc_dir, "scripts")
    shops_dir = os.path.join(npc_dir, "shops")

    os.makedirs(npc_dir, exist_ok=True)
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(shops_dir, exist_ok=True)

    return npc_dir, scripts_dir, shops_dir


def create_npc_xml(
    npc: Npc, npc_dir: str, file_base: str, outfit: Dict[str, Any], has_shop: bool
) -> None:
    """
    Create the NPC .xml file in the city's folder.
    """
    npc_name = npc.name or npc.title
    npc_xml_path = os.path.join(npc_dir, f"{file_base}.xml")

    # Coordinates may be None for some NPCs
    home_x = npc.x if npc.x is not None else 0
    home_y = npc.y if npc.y is not None else 0
    home_z = npc.z if npc.z is not None else 7

    # Script and shop file names (relative to the city's NPC XML location)
    script_file = f"scripts/{file_base}.lua"
    shop_file = f"shops/{file_base}.shop"

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<npc name="{xml_escape_attr(npc_name)}" '
        f'script="{xml_escape_attr(script_file)}" '
        f'walkinterval="2000" floorchange="0">'
    )
    lines.append('\t<health now="100" max="100" />')
    lines.append(
        f'\t<look type="{outfit["type"]}" '
        f'head="{outfit["head"]}" '
        f'body="{outfit["body"]}" '
        f'legs="{outfit["legs"]}" '
        f'feet="{outfit["feet"]}" '
        f'addons="{outfit.get("addons", 0)}" />'
    )
    lines.append(f'\t<home x="{home_x}" y="{home_y}" z="{home_z}"/>')

    if has_shop:
        lines.append("\t<parameters>")
        lines.append('\t\t<parameter key="module_shop" value="1" />')
        lines.append(
            f'\t\t<parameter key="shop_file" '
            f'value="{xml_escape_attr(shop_file)}" />'
        )
        lines.append("\t</parameters>")

    lines.append("</npc>")
    contents = "\n".join(lines)

    with open(npc_xml_path, "w", encoding="utf-8") as f:
        f.write(contents)


def create_shop_xml(
    shops_dir: str,
    file_base: str,
    buyable_entries: List[Tuple[str, int, int]],
    sellable_entries: List[Tuple[str, int, int]],
) -> None:
    """
    Create the shop .shop XML file for one NPC in the city's shops folder.
    """
    shop_path = os.path.join(shops_dir, f"{file_base}.shop")

    buyable_str = build_shop_value(buyable_entries)
    sellable_str = build_shop_value(sellable_entries)

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<shop>")
    lines.append("\t<parameters>")

    if buyable_entries:
        lines.append(
            '\t\t<parameter key="shop_buyable" value="'
            + buyable_str.replace('"', "&quot;")
            + '" />'
        )

    if sellable_entries:
        lines.append(
            '\t\t<parameter key="shop_sellable" value="'
            + sellable_str.replace('"', "&quot;")
            + '" />'
        )

    lines.append("\t</parameters>")
    lines.append("</shop>")

    contents = "\n".join(lines)

    with open(shop_path, "w", encoding="utf-8") as f:
        f.write(contents)


def create_lua_script(scripts_dir: str, file_base: str, npc_name: str) -> None:
    """
    Create a simple Lua script for the NPC (no 'first rod' logic, just basic NPCSystem),
    in the city's scripts folder.
    """
    lua_path = os.path.join(scripts_dir, f"{file_base}.lua")

    lua_code = f"""local keywordHandler = KeywordHandler:new()
local npcHandler = NpcHandler:new(keywordHandler)
NpcSystem.parseParameters(npcHandler)

function onCreatureAppear(cid) npcHandler:onCreatureAppear(cid) end
function onCreatureDisappear(cid) npcHandler:onCreatureDisappear(cid) end
function onCreatureSay(cid, type, msg) npcHandler:onCreatureSay(cid, type, msg) end
function onThink() npcHandler:onThink() end

local function creatureSayCallback(cid, type, msg)
    if not npcHandler:isFocused(cid) then
        return false
    end
    -- Add custom conversation logic for {npc_name} here if you want.
    return true
end

npcHandler:setCallback(CALLBACK_MESSAGE_DEFAULT, creatureSayCallback)
npcHandler:addModule(FocusModule:new())
"""

    with open(lua_path, "w", encoding="utf-8") as f:
        f.write(lua_code)


def main():
    # Root output folder
    os.makedirs(OUTPUT_ROOT, exist_ok=True)

    # Load optional outfits.json
    outfits_map = load_outfits(OUTFITS_JSON)

    # Connect to tibiawiki.db
    conn = sqlite3.connect(DB_PATH)

    # Get all NPC article_ids from the npc table
    cur = conn.cursor()
    cur.execute("SELECT article_id FROM npc")
    npc_ids = [row[0] for row in cur.fetchall()]
    print(f"Found {len(npc_ids)} NPCs in the database.")

    for article_id in npc_ids:
        npc = Npc.get_one_by_field(conn, "article_id", article_id)
        if npc is None:
            continue

        npc_name = npc.name or npc.title
        file_base = slugify_name(npc_name)

        # City folder
        city_name = npc.city or ""
        city_slug = slugify_city(city_name)
        npc_dir, scripts_dir, shops_dir = ensure_city_dirs(city_slug)

        # Outfit
        outfit = get_outfit_for_npc(npc_name, outfits_map)

        # Build shop entries using NPC offers and Item.client_id
        buyable_entries: List[Tuple[str, int, int]] = []  # NPC sells to player
        sellable_entries: List[Tuple[str, int, int]] = []  # NPC buys from player

        # NPC SELL offers = items you can BUY from NPC
        for offer in npc.sell_offers or []:
            item = Item.get_one_by_field(conn, "article_id", offer.item_id)
            if not item or item.client_id is None:
                continue
            item_name = (item.actual_name or item.name or offer.item_title).lower()
            buyable_entries.append((item_name, item.client_id, offer.value))

        # NPC BUY offers = items you can SELL to NPC
        for offer in npc.buy_offers or []:
            item = Item.get_one_by_field(conn, "article_id", offer.item_id)
            if not item or item.client_id is None:
                continue
            item_name = (item.actual_name or item.name or offer.item_title).lower()
            sellable_entries.append((item_name, item.client_id, offer.value))

        has_shop = bool(buyable_entries or sellable_entries)

        # Generate files under that city's folder
        create_npc_xml(npc, npc_dir, file_base, outfit, has_shop=has_shop)

        if has_shop:
            create_shop_xml(shops_dir, file_base, buyable_entries, sellable_entries)

        create_lua_script(scripts_dir, file_base, npc_name)

        print(
            f"[{city_slug}] Generated NPC: {npc_name} -> {file_base}.xml / .lua / .shop"
        )

    conn.close()
    print("Done. Files written under:", OUTPUT_ROOT)


if __name__ == "__main__":
    main()
