# Antigravity Workspace Directives

## Strictly Read-Only Policy for Skill Execution Engine
- **Scripts Directory is Read-Only**: All Python files in `scripts/` (including `main.py`, `places_searcher.py`, `filters.py`, `directional_router.py`, `route_generator.py`, `sheet_exporter.py`, etc.) are part of a fixed, pre-compiled, deterministically tested lead-scout execution engine.
- **NEVER Modify Scripts for User Tasks**: When the user requests prospecting, route planning, provides map screenshots, pins, addresses, or requests searching specific areas or commercial centers (e.g., Markville Mall, Hwy 7, etc.), **NEVER** modify, patch, rewrite, or hardcode values into the Python scripts or configuration templates.
- **Always Use CLI Arguments**: Map user visual or text requests dynamically to existing CLI parameters:
  - Departure Base / Origin: `--origin "<address/landmark/coordinates>"`
  - Target Search District (Decoupled from Origin): `--search-center "<landmark/coordinates>"`
  - Target Search Radius: `--search-radius <km>` or `--radius <km>`
  - Geofence Bounding Box: `--bounds "<min_lat,min_lng,max_lat,max_lng>"`
  - Custom Keywords: `--keywords "fried chicken,wings,fast food,ramen"`
  - Radial Search: `--direction radial --radius <km>`
  - Directional Search: `--direction <east|west|north|south|etc.>`
  - Target Count: `--count <N>`
  - Exclude/Include Regions: `--exclude-regions "..."` / `--include-regions "..."`
  - Specific Restaurants: `--include-restaurants "..."` / `--exclude-restaurants "..."`
- The agent must act strictly as a tool operator executing commands via CLI, NOT as a code modifier, unless the user explicitly commands: "Modify the skill code to add a new feature".

## Handling Visual Map Screenshots & Remote Search Centers
- When the user provides an image or screenshot of a map:
  1. Inspect the image to determine the approximate center coordinates (latitude, longitude) or landmark name.
  2. Estimate the search radius (e.g. 1.5 km, 2.0 km) and optional bounding box (`min_lat,min_lng,max_lat,max_lng`).
  3. If user specifies a departure base (e.g. "以 Field Operations Base 为出发地址，在截图范围内找 30 家店"):
     ```bash
     python3 scripts/main.py \
       --origin "Field Operations Base" \
       --search-center "<lat>,<lng>" \
       --search-radius <radius_km> \
       --bounds "<min_lat,min_lng,max_lat,max_lng>" \
       --count 30
     ```
  4. If only the map area is given:
     ```bash
     python3 scripts/main.py \
       --search-center "<lat>,<lng>" \
       --search-radius <radius_km> \
       --bounds "<min_lat,min_lng,max_lat,max_lng>"
     ```
  5. Do not modify any Python files under any circumstances.

## Dynamic Departure Date & Time Resolution
- **User Local Time Anchoring**: Check the user's current local date and time from the system context (`<ADDITIONAL_METADATA>` or current system clock).
- **Natural Language Parsing**:
  - If user mentions explicit times ("明天上午10点", "今天下午2点", "明早9点半", "现在出发"): resolve into 24-hour format `--departure-time "HH:MM"` and corresponding `--visit-date <today|tomorrow|YYYY-MM-DD>`.
  - If user requests planning for **today** ("今天") or immediate departure without an explicit time: always inject `--visit-date today --departure-time "<current_HH:MM>"` using current local time to avoid defaulting to 09:30 AM in the past.
  - If user requests planning for **tomorrow** ("明天") without an explicit time: inject `--visit-date tomorrow` (defaults to standard morning 09:30).
