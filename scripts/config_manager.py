#!/usr/bin/env python3
"""
Configuration and Setup Manager for Daily Restaurant Lead Scout.
Handles:
1. Cross-platform user-level configuration (~/.config/restaurant_lead_scout/config.json).
2. Local project fallback configuration (./config.json).
3. Zero-code interactive setup wizard for non-technical users.
4. Storage and validation of Google Apps Script Webhook URL for Google Sheet synchronization.
5. Safe resolution of system temporary directory for intermediate process files.
"""

import os
import sys
import re
import json
import tempfile
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional

USER_CONFIG_DIR = os.path.expanduser("~/.config/restaurant_lead_scout")
USER_CONFIG_FILE = os.path.join(USER_CONFIG_DIR, "config.json")
USER_CACHE_DIR = os.path.expanduser("~/.cache/restaurant_lead_scout")

def find_project_root(start_dir: Optional[str] = None) -> Optional[str]:
    """
    Intelligently determines whether the skill is installed/running within a dedicated project workspace.
    Returns the absolute path to the project root, or None if in root/global mode.

    Detection logic:
    1. Parent directory inspection:
       If skill is located in <project>/.gemini/skills/<name>, <project>/skills/<name>, or <project>/.claude/skills/<name>,
       and <project> is NOT the user's home directory (~) or root (/), then <project> is the project root.
    2. Standalone cloned workspace repo:
       If the skill directory itself contains .git or pyproject.toml and is not inside ~/.gemini, ~/.config, ~/.claude,
       then the skill directory itself is the project workspace.
    3. Working directory inspection (CWD):
       If current working directory is an active project workspace (contains .git, .gemini, pyproject.toml, package.json)
       and is NOT the user's home directory.
    """
    home_dir = os.path.realpath(os.path.expanduser("~"))
    base_dir = os.path.realpath(start_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Check 1: Parent hierarchy for .agents/skills, .gemini/skills, skills, .claude/skills
    cur = base_dir
    for _ in range(5):
        parent = os.path.dirname(cur)
        if not parent or parent == "/" or parent == home_dir:
            break
        if os.path.basename(parent) in (".gemini", "skills", ".claude", ".agents", "_agents"):
            project_candidate = os.path.dirname(parent)
            # Strip agent/wrapper directories (e.g., .agents, _agents, .gemini, .claude, antigravity)
            while project_candidate and os.path.basename(project_candidate) in (
                ".agents", ".agent", "_agents", "_agent", ".gemini", ".claude", "antigravity"
            ):
                project_candidate = os.path.dirname(project_candidate)
            if project_candidate and project_candidate != home_dir and project_candidate != "/":
                return project_candidate
        cur = parent

    # Check 2: Is base_dir itself a project root?
    if (base_dir != home_dir and
        not base_dir.startswith(os.path.join(home_dir, ".gemini")) and
        not base_dir.startswith(os.path.join(home_dir, ".config")) and
        not base_dir.startswith(os.path.join(home_dir, ".claude")) and
        not base_dir.startswith(os.path.join(home_dir, ".agents")) and
        not base_dir.startswith(os.path.join(home_dir, "_agents"))):
        if (os.path.exists(os.path.join(base_dir, ".git")) or
            os.path.exists(os.path.join(base_dir, "pyproject.toml")) or
            os.path.exists(os.path.join(base_dir, "requirements.txt"))):
            return base_dir

    # Check 3: Current working directory (CWD)
    cwd = os.path.realpath(os.getcwd())
    if (cwd != home_dir and cwd != "/" and
        not cwd.startswith(os.path.join(home_dir, ".gemini")) and
        not cwd.startswith(os.path.join(home_dir, ".agents")) and
        not cwd.startswith(os.path.join(home_dir, "_agents")) and
        not cwd.startswith(os.path.join(home_dir, ".claude"))):
        if (os.path.exists(os.path.join(cwd, ".git")) or
            os.path.exists(os.path.join(cwd, ".agents")) or
            os.path.exists(os.path.join(cwd, "_agents")) or
            os.path.exists(os.path.join(cwd, ".gemini")) or
            os.path.exists(os.path.join(cwd, "pyproject.toml")) or
            os.path.exists(os.path.join(cwd, "package.json"))):
            return cwd

    return None

def get_effective_storage_paths(custom_config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns dynamic paths based on the detected runtime mode:
    - Project Mode: Config, cache & temp files stay 100% inside <project_root>, zero pollution to ~/.
    - Global Mode: Config & cache stay in standard ~/.config and ~/.cache (XDG compliant).
    """
    project_root = find_project_root()
    is_project = project_root is not None

    if is_project:
        config_file = custom_config_path or os.path.join(project_root, "config.json")
        cache_dir = os.path.join(project_root, ".cache", "restaurant_lead_scout")
        temp_dir = os.path.join(project_root, ".cache", "restaurant_lead_scout", "audit")
        output_dir = os.path.join(project_root, "output")
    else:
        config_file = custom_config_path or USER_CONFIG_FILE
        cache_dir = USER_CACHE_DIR
        temp_dir = os.path.join(tempfile.gettempdir(), "restaurant_lead_scout", "audit")
        output_dir = os.path.join(USER_CACHE_DIR, "routes")

    return {
        "is_project_mode": is_project,
        "project_root": project_root,
        "mode_label": "Project Workspace" if is_project else "Global / Root",
        "config_file": config_file,
        "cache_dir": cache_dir,
        "temp_dir": temp_dir,
        "output_dir": output_dir
    }

def get_default_config_path() -> str:
    """Returns the effective default config file path."""
    return get_effective_storage_paths()["config_file"]

def get_temp_dir(subfolder: str = "audit") -> str:
    """Returns directory for intermediate files, isolated in project if available or system temp."""
    paths = get_effective_storage_paths()
    if paths["is_project_mode"]:
        target_dir = os.path.join(paths["project_root"], ".cache", "restaurant_lead_scout", subfolder) if subfolder else paths["cache_dir"]
    else:
        base_tmp = os.path.join(tempfile.gettempdir(), "restaurant_lead_scout")
        target_dir = os.path.join(base_tmp, subfolder) if subfolder else base_tmp
    os.makedirs(target_dir, exist_ok=True)
    return target_dir

def get_user_cache_dir(subfolder: str = "routes") -> str:
    """Returns persistent cache directory, isolated in project if available or ~/.cache."""
    paths = get_effective_storage_paths()
    if paths["is_project_mode"]:
        target_dir = os.path.join(paths["project_root"], "output") if subfolder in ("routes", "output") else os.path.join(paths["cache_dir"], subfolder)
    else:
        target_dir = os.path.join(USER_CACHE_DIR, subfolder) if subfolder else USER_CACHE_DIR
    os.makedirs(target_dir, exist_ok=True)
    return target_dir

def extract_spreadsheet_id(url_or_id: Optional[str]) -> str:
    """
    Extracts canonical Google Spreadsheet ID from either:
    - Full browser URL: https://docs.google.com/spreadsheets/d/1BxiMVs0XR.../edit#gid=0
    - Share link: https://docs.google.com/spreadsheets/d/e/2PACX.../pubhtml
    - Raw ID string: 1BxiMVs0XR...
    """
    if not url_or_id:
        return ""
    cleaned = str(url_or_id).strip()
    match = re.search(r"/spreadsheets/d/(?:e/)?([a-zA-Z0-9-_]+)", cleaned)
    if match:
        return match.group(1)
    # Strip any query parameters or hash if passed raw
    cleaned = cleaned.split("?")[0].split("#")[0].strip("/")
    return cleaned

GTA_PRESET_LANDMARKS = {
    "north york": (43.7615, -79.4111, "North York Centre, Toronto, ON"),
    "fairview mall": (43.7780, -79.3440, "Fairview Mall, 1800 Sheppard Ave E, Toronto, ON"),
    "markham": (43.8561, -79.3370, "Markham, ON, Canada"),
    "scarborough": (43.7764, -79.2318, "Scarborough Town Centre, Toronto, ON"),
    "downtown": (43.6532, -79.3832, "Downtown Toronto, ON, Canada"),
    "richmond hill": (43.8828, -79.4403, "Richmond Hill, ON, Canada"),
    "mississauga": (43.5890, -79.6441, "Mississauga, ON, Canada"),
    "vaughan": (43.8563, -79.5085, "Vaughan, ON, Canada"),
    "markville mall": (43.8682, -79.2883, "CF Markville, 5000 Hwy 7, Markham, ON"),
    "cf markville": (43.8682, -79.2883, "CF Markville, 5000 Hwy 7, Markham, ON"),
    "pacific mall": (43.8258, -79.3064, "Pacific Mall, 4300 Steeles Ave E, Markham, ON"),
}

def resolve_origin_input(origin_input: Optional[str], api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Intelligently parses user origin input from:
    1. Google Maps URL (maps.app.goo.gl, google.com/maps/@lat,lng, etc.)
    2. Coordinates ("43.7615, -79.4111")
    3. Plain text address or landmark ("Fairview Mall", "North York, ON", "123 Finch Ave E")
    """
    if not origin_input:
        return {
            "name": "Field Operations Base",
            "address": "North York, Toronto, ON",
            "latitude": 43.7615,
            "longitude": -79.4111
        }

    raw = str(origin_input).strip()

    # 1. Format: Direct Latitude, Longitude (e.g. "43.7615, -79.4111" or "43.7615,-79.4111")
    coord_match = re.match(r"^\s*([+-]?\d+\.?\d*)\s*,\s*([+-]?\d+\.?\d*)\s*$", raw)
    if coord_match:
        lat = float(coord_match.group(1))
        lng = float(coord_match.group(2))
        return {
            "name": f"Pin ({lat:.4f}, {lng:.4f})",
            "address": f"{lat:.6f}, {lng:.6f}",
            "latitude": lat,
            "longitude": lng
        }

    # 2. Format: Google Maps URL (full web link or mobile app share link)
    if any(domain in raw for domain in ["google.com/maps", "maps.google.com", "maps.app.goo.gl", "goo.gl/maps"]):
        url = raw
        # If it's a short link like https://maps.app.goo.gl/..., resolve redirect
        if "maps.app.goo.gl" in url or "goo.gl/maps" in url:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    url = resp.geturl()
            except Exception:
                pass

        at_match = re.search(r"@([+-]?\d+\.\d+),([+-]?\d+\.\d+)", url)
        q_match = re.search(r"[?&](?:q|query|ll)=([+-]?\d+\.\d+),([+-]?\d+\.\d+)", url)

        extracted_lat = None
        extracted_lng = None
        if at_match:
            extracted_lat = float(at_match.group(1))
            extracted_lng = float(at_match.group(2))
        elif q_match:
            extracted_lat = float(q_match.group(1))
            extracted_lng = float(q_match.group(2))

        place_name = "Google Maps Location"
        place_match = re.search(r"/maps/place/([^/@?]+)", url)
        if place_match:
            place_name = urllib.parse.unquote_plus(place_match.group(1)).replace("+", " ")

        if extracted_lat is not None and extracted_lng is not None:
            return {
                "name": place_name,
                "address": place_name,
                "latitude": extracted_lat,
                "longitude": extracted_lng
            }

        # If coordinates not found in URL but place name extracted, use place name as query
        raw = place_name

    # 3. Format: Text Address or Landmark Name
    # 3a. Check if matches GTA preset landmarks (exact match only, to prevent intercepting specific street addresses)
    raw_lower = raw.lower().strip()
    raw_cleaned = re.sub(r",\s*(on|ontario|canada|ca)\b", "", raw_lower).strip()
    if raw_cleaned in GTA_PRESET_LANDMARKS or raw_lower in GTA_PRESET_LANDMARKS:
        target_key = raw_cleaned if raw_cleaned in GTA_PRESET_LANDMARKS else raw_lower
        val = GTA_PRESET_LANDMARKS[target_key]
        return {
            "name": target_key.title(),
            "address": val[2],
            "latitude": val[0],
            "longitude": val[1]
        }

    # 3b. If Google Maps API Key is active, query Google Places API searchText
    if api_key and not api_key.startswith("YOUR_"):
        try:
            req_url = "https://places.googleapis.com/v1/places:searchText"
            payload = {
                "textQuery": raw,
                "maxResultCount": 1,
                "languageCode": "en",
                "regionCode": "CA"
            }
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(req_url, data=req_data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Goog-Api-Key", api_key)
            if os.environ.get("GOOGLE_MAPS_API_REFERER"):
                req.add_header("Referer", os.environ.get("GOOGLE_MAPS_API_REFERER").strip())
            req.add_header("X-Goog-FieldMask", "places.displayName,places.formattedAddress,places.location")

            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                places = data.get("places", [])
                if places:
                    p0 = places[0]
                    disp = (p0.get("displayName") or {}).get("text") or raw
                    addr = p0.get("formattedAddress") or raw
                    loc = p0.get("location") or {}
                    lat = float(loc.get("latitude", 43.7615))
                    lng = float(loc.get("longitude", -79.4111))
                    return {
                        "name": disp,
                        "address": addr,
                        "latitude": lat,
                        "longitude": lng
                    }
        except Exception as e:
            print(f"[Warning] Geocoding search warning for '{raw}': {e}")

    # 3c. Fallback
    return {
        "name": raw,
        "address": raw,
        "latitude": 43.7615,
        "longitude": -79.4111
    }

def find_default_template_path() -> str:
    """Finds config.example.json relative to repository structure."""
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(cur_dir)
    example_path = os.path.join(repo_root, "config.example.json")
    if os.path.exists(example_path):
        return example_path
    local_config = os.path.join(repo_root, "config.json")
    if os.path.exists(local_config):
        return local_config
    return ""

def load_base_template() -> Dict[str, Any]:
    tpl_path = find_default_template_path()
    if tpl_path and os.path.exists(tpl_path):
        try:
            with open(tpl_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "google_maps_api_key": "",
        "typesafe_api_key": "",
        "openrouter_api_key": "",
        "language_code": "en",
        "default_origin": {
            "name": "Field Operations Base",
            "address": "North York, Toronto, ON",
            "latitude": 43.7615,
            "longitude": -79.4111
        },
        "target_count": 30,
        "corridor_settings": {
            "default_direction": "east",
            "corridor_width_km": 4.5,
            "step_distance_km": 3.0,
            "max_search_depth_km": 28.0,
            "backtrack_penalty_weight": 50.0
        },
        "google_sheets": {
            "enabled": False,
            "webhook_url": "",
            "spreadsheet_id": "",
            "spreadsheet_url": "",
            "sheet_name": "Daily Field Sales Route"
        }
    }

def get_config_summary(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Returns clean human- and agent-readable status of the current user configuration."""
    paths = get_effective_storage_paths(custom_config_path=config_path)
    cfg = load_effective_config(custom_config_path=paths["config_file"], allow_interactive=False)
    cur_api_key = cfg.get("google_maps_api_key", "").strip()
    cur_typesafe_key = (
        cfg.get("typesafe_api_key", "").strip()
        or cfg.get("jev_api_key", "").strip()
        or os.environ.get("TYPESAFE_API_KEY", "").strip()
        or os.environ.get("JEV_API_KEY", "").strip()
    )
    cur_openrouter_key = cur_typesafe_key or cfg.get("openrouter_api_key", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()
    effective_jev_key = cur_typesafe_key or cur_openrouter_key
    gs_cfg = cfg.get("google_sheets", {})
    cur_sheet_id = gs_cfg.get("spreadsheet_id", "").strip()
    cur_sheet_url = gs_cfg.get("spreadsheet_url", "").strip()
    cur_webhook = gs_cfg.get("webhook_url", "").strip() or cur_sheet_url or cur_sheet_id
    is_sheet_configured = bool(cur_webhook and not cur_webhook.startswith("YOUR_"))
    sheet_display = cur_sheet_url or (f"https://docs.google.com/spreadsheets/d/{cur_sheet_id}" if cur_sheet_id else "(未配置)")
    origin = cfg.get("default_origin", {})

    return {
        "mode": paths["mode_label"],
        "is_project_mode": paths["is_project_mode"],
        "project_root": paths["project_root"],
        "config_file": paths["config_file"],
        "temp_audit_dir": paths["temp_dir"],
        "output_dir": paths["output_dir"],
        "api_key_configured": bool(cur_api_key and not cur_api_key.startswith("YOUR_")),
        "api_key_masked": f"{cur_api_key[:6]}...{cur_api_key[-4:]}" if len(cur_api_key) > 10 else ("(已配置)" if cur_api_key else "(未配置)"),
        "typesafe_api_key_configured": bool(effective_jev_key and not effective_jev_key.startswith("YOUR_") and len(effective_jev_key) > 8),
        "typesafe_key_masked": f"{effective_jev_key[:8]}...{effective_jev_key[-4:]}" if len(effective_jev_key) > 12 else ("(已配置)" if effective_jev_key else "(未配置)"),
        "openrouter_api_key_configured": bool(cur_openrouter_key and not cur_openrouter_key.startswith("YOUR_") and len(cur_openrouter_key) > 8),
        "openrouter_key_masked": f"{cur_openrouter_key[:8]}...{cur_openrouter_key[-4:]}" if len(cur_openrouter_key) > 12 else ("(已配置)" if cur_openrouter_key else "(未配置)"),
        "google_sheet_configured": is_sheet_configured,
        "google_sheet_url": sheet_display,
        "google_sheet_webhook": cur_webhook,
        "google_sheet_id": cur_sheet_id or cur_webhook,
        "default_origin": {
            "name": origin.get("name", "Field Operations Base"),
            "address": origin.get("address", "North York, Toronto, ON"),
            "latitude": origin.get("latitude", 43.7615),
            "longitude": origin.get("longitude", -79.4111)
        }
    }

def update_config_values(
    api_key: Optional[str] = None,
    sheet_url: Optional[str] = None,
    origin_input: Optional[str] = None,
    openrouter_key: Optional[str] = None,
    typesafe_key: Optional[str] = None,
    jev_key: Optional[str] = None,
    config_save_path: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Non-interactive configuration updater designed for AI agents and automated scripts.
    Allows zero-code / zero-terminal updates directly from model dialog inputs.
    """
    if "config_path" in kwargs and not config_save_path:
        config_save_path = kwargs["config_path"]
    if "origin_str" in kwargs and not origin_input:
        origin_input = kwargs["origin_str"]

    effective_jev_key = typesafe_key or jev_key or openrouter_key
    if "typesafe_api_key" in kwargs and not effective_jev_key:
        effective_jev_key = kwargs["typesafe_api_key"]
    if "jev_api_key" in kwargs and not effective_jev_key:
        effective_jev_key = kwargs["jev_api_key"]
    if "openrouter_api_key" in kwargs and not effective_jev_key:
        effective_jev_key = kwargs["openrouter_api_key"]

    paths = get_effective_storage_paths(custom_config_path=config_save_path)
    save_file = paths["config_file"]
    cfg: Dict[str, Any] = {}
    if os.path.exists(save_file):
        try:
            with open(save_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    if not cfg:
        cfg = load_effective_config(custom_config_path=save_file, allow_interactive=False)

    if api_key is not None:
        cfg["google_maps_api_key"] = str(api_key).strip()

    if effective_jev_key is not None:
        cfg["typesafe_api_key"] = str(effective_jev_key).strip()
        cfg["openrouter_api_key"] = str(effective_jev_key).strip()

    if sheet_url is not None:
        sheet_str = str(sheet_url).strip()
        gs_cfg = cfg.setdefault("google_sheets", {})
        if sheet_str:
            extracted_id = extract_spreadsheet_id(sheet_str)
            gs_cfg["spreadsheet_id"] = extracted_id
            gs_cfg["spreadsheet_url"] = sheet_str if sheet_str.startswith("http") else f"https://docs.google.com/spreadsheets/d/{extracted_id}"
            gs_cfg["webhook_url"] = sheet_str
            gs_cfg["enabled"] = True
        else:
            gs_cfg["webhook_url"] = ""
            gs_cfg["spreadsheet_url"] = ""
            gs_cfg["spreadsheet_id"] = ""
            gs_cfg["enabled"] = False

    if origin_input is not None:
        origin_str = str(origin_input).strip()
        if origin_str:
            resolved = resolve_origin_input(origin_str, api_key=cfg.get("google_maps_api_key"))
            origin = cfg.setdefault("default_origin", {})
            origin["name"] = resolved["name"]
            origin["address"] = resolved["address"]
            origin["latitude"] = resolved["latitude"]
            origin["longitude"] = resolved["longitude"]

    os.makedirs(os.path.dirname(save_file), exist_ok=True)
    with open(save_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return cfg

def run_interactive_setup(existing_cfg: Optional[Dict[str, Any]] = None, config_save_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Guides the user through an interactive terminal wizard to set up API keys and sheet links.
    Saves directly to project config or user configuration file.
    """
    paths = get_effective_storage_paths(custom_config_path=config_save_path)
    save_file = paths["config_file"]
    cfg = existing_cfg or load_effective_config(custom_config_path=save_file, allow_interactive=False)

    if not sys.stdin.isatty():
        print("[Notice] 检测到非交互式终端环境，无法启动基于终端的键盘输入向导。")
        print("[Info] 请使用非交互式配置参数，例如: python3 scripts/main.py --configure --set-api-key KEY --set-sheet-url URL --set-origin ORIGIN")
        return cfg

    print("\n" + "=" * 70)
    print("欢迎使用 Daily Restaurant Lead Scout (配置向导)")
    print("=" * 70)
    print(f"运行模式: {paths['mode_label']}")
    print(f"配置文件保存路径: {save_file}\n")

    # 1. Google Maps API Key
    cur_api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or cfg.get("google_maps_api_key", "")
    masked_key = f"{cur_api_key[:6]}...{cur_api_key[-4:]}" if len(cur_api_key) > 10 else "(未配置)"
    print(f"[1/4] Google Maps Places API Key")
    print(f"      当前状态: {masked_key}")
    key_input = input("请输入您的 Google Maps API Key [直接回车保持现有]: ").strip()
    if key_input:
        cfg["google_maps_api_key"] = key_input
    elif not cur_api_key:
        cfg["google_maps_api_key"] = ""

    # 2. TypeSafe AI API Key (Jev Decision Model)
    cur_jev_key = (
        os.environ.get("TYPESAFE_API_KEY")
        or os.environ.get("JEV_API_KEY")
        or cfg.get("typesafe_api_key", "")
        or os.environ.get("OPENROUTER_API_KEY")
        or cfg.get("openrouter_api_key", "")
    )
    masked_jev = f"{cur_jev_key[:8]}...{cur_jev_key[-4:]}" if len(cur_jev_key) > 12 else "(未配置)"
    print(f"\n[2/4] TypeSafe API Key (用于 official Jev 决策模型: POST https://api.typesafe.ai/v1/systemone)")
    print(f"      当前状态: {masked_jev}")
    jev_input = input("请输入您的 TypeSafe API Key (直接回车保持/跳过): ").strip()
    if jev_input:
        cfg["typesafe_api_key"] = jev_input
        cfg["openrouter_api_key"] = jev_input
    elif not cur_jev_key:
        cfg["typesafe_api_key"] = ""
        cfg["openrouter_api_key"] = ""

    # 3. Google Sheet Target Apps Script Webhook URL
    gs_cfg = cfg.setdefault("google_sheets", {})
    cur_webhook = gs_cfg.get("webhook_url") or gs_cfg.get("spreadsheet_url") or gs_cfg.get("spreadsheet_id", "")
    display_sheet = cur_webhook if cur_webhook else "(未配置)"
    print(f"\n[3/4] 目标 Google Sheet (仅支持 Google Apps Script Webhook 零凭据免密写入)")
    print(f"      当前状态: {display_sheet}")
    sheet_input = input("请输入您的 Google Apps Script Webhook URL (例如: https://script.google.com/macros/s/.../exec，回车保持/跳过): ").strip()
    if sheet_input:
        gs_cfg["webhook_url"] = sheet_input
        gs_cfg["spreadsheet_url"] = sheet_input
        gs_cfg["spreadsheet_id"] = sheet_input
        gs_cfg["enabled"] = True
    elif sheet_input == "" and cur_webhook:
        # Keep existing
        gs_cfg["enabled"] = True

    # 4. Default Origin Base
    origin = cfg.setdefault("default_origin", {})
    cur_origin_name = origin.get("name", "Field Operations Base")
    cur_origin_addr = origin.get("address", "North York, Toronto, ON")
    cur_lat = origin.get("latitude", 43.7615)
    cur_lng = origin.get("longitude", -79.4111)
    print(f"\n[4/4] 默认出发拓客起点")
    print(f"      当前起点: {cur_origin_name} - {cur_origin_addr} ({cur_lat:.4f}, {cur_lng:.4f})")
    addr_input = input(f"请输入出发起点 (支持文本地址、Google 地图链接或经纬度坐标) [直接回车保持现有]: ").strip()
    if addr_input:
        resolved = resolve_origin_input(addr_input, api_key=cfg.get("google_maps_api_key"))
        origin["name"] = resolved["name"]
        origin["address"] = resolved["address"]
        origin["latitude"] = resolved["latitude"]
        origin["longitude"] = resolved["longitude"]
        print(f"      起点已自动解析定位: {resolved['name']} ({resolved['latitude']:.4f}, {resolved['longitude']:.4f})")

    # Save to configuration
    os.makedirs(os.path.dirname(save_file), exist_ok=True)
    with open(save_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print("\n" + "-" * 70)
    print(f"配置已成功保存！路径: {save_file}")
    if cfg.get("google_maps_api_key"):
        print("  Google Maps API: 已就绪 (在线搜索模式)")
    else:
        print("  Google Maps API: 未配置")
    if cfg.get("typesafe_api_key") or cfg.get("openrouter_api_key"):
        print("  TypeSafe (Jev 决策模型): 已就绪 (官方 SystemOne API 增强模式)")
    else:
        print("  TypeSafe (Jev 决策模型): 未配置 (降级为本地启发式规则)")
    cur_webhook = gs_cfg.get("webhook_url") or gs_cfg.get("spreadsheet_url") or gs_cfg.get("spreadsheet_id", "")
    if gs_cfg.get("enabled") and cur_webhook:
        print(f"  Google Sheet Webhook: {cur_webhook}")
    print("-" * 70 + "\n")
    return cfg

def load_effective_config(custom_config_path: Optional[str] = None, allow_interactive: bool = True) -> Dict[str, Any]:
    """
    Loads configuration adhering to the precedence hierarchy:
    1. Custom path if provided via --config.
    2. Detected project-mode config (<project_root>/config.json).
    3. User global config (~/.config/restaurant_lead_scout/config.json).
    4. Auto-triggers interactive setup if run in interactive terminal and unconfigured.
    5. Fallback template.
    """
    paths = get_effective_storage_paths(custom_config_path=custom_config_path)
    target_config = paths["config_file"]
    cfg: Dict[str, Any] = {}

    # 1. Custom or detected mode config file
    if os.path.exists(target_config):
        try:
            with open(target_config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[Warning] Could not parse config {target_config}: {e}")

    # 2. In project mode, check project root config.json if not already loaded
    if not cfg and paths["is_project_mode"]:
        proj_cfg = os.path.join(paths["project_root"], "config.json")
        if os.path.exists(proj_cfg):
            try:
                with open(proj_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass

    # 3. Global user config fallback
    if not cfg and os.path.exists(USER_CONFIG_FILE):
        try:
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    # 4. If still empty, load template
    if not cfg:
        cfg = load_base_template()

    # Environment variables override
    env_api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if env_api_key:
        cfg["google_maps_api_key"] = env_api_key.strip()

    env_typesafe_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
    if env_typesafe_key:
        cfg["typesafe_api_key"] = env_typesafe_key.strip()
        cfg["openrouter_api_key"] = env_typesafe_key.strip()

    env_openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if env_openrouter_key:
        if "typesafe_api_key" not in cfg or not cfg["typesafe_api_key"]:
            cfg["typesafe_api_key"] = env_openrouter_key.strip()
        cfg["openrouter_api_key"] = env_openrouter_key.strip()

    env_sheet_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL") or os.environ.get("GOOGLE_SHEET_URL") or os.environ.get("GOOGLE_SHEET_ID")
    if env_sheet_url:
        gs = cfg.setdefault("google_sheets", {})
        cleaned_url = env_sheet_url.strip()
        gs["webhook_url"] = cleaned_url
        gs["spreadsheet_url"] = cleaned_url
        gs["spreadsheet_id"] = cleaned_url
        gs["enabled"] = True

    # 5. Check if auto interactive setup should be triggered
    # If not configured, and user is running interactively in terminal (stdin is tty)
    is_configured = bool(cfg.get("google_maps_api_key")) or bool(cfg.get("typesafe_api_key")) or bool(cfg.get("openrouter_api_key")) or bool(cfg.get("google_sheets", {}).get("webhook_url")) or bool(cfg.get("google_sheets", {}).get("spreadsheet_id"))
    if not is_configured and allow_interactive and sys.stdin.isatty():
        try:
            cfg = run_interactive_setup(cfg)
        except (KeyboardInterrupt, EOFError):
            print("\n[Notice] 交互式设置已跳过，继续运行。")

    return cfg
