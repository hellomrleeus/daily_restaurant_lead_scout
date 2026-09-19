#!/usr/bin/env python3
"""
Route generator module.
Builds the full 30-stop slash-concatenated Google Maps URL that bypasses
the standard 10-stop restriction:
https://www.google.com/maps/dir/{origin}/{stop1}/{stop2}/.../{stop30}/

Also calculates visit schedule and optional segmented legs for mobile convenience.
"""

import math
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
from directional_router import haversine_distance_km

try:
    from sheet_exporter import cluster_navigation_addresses
except ImportError:
    def cluster_navigation_addresses(stops):
        return stops

MAX_WAYPOINTS_PER_LEG = 9
AVERAGE_URBAN_SPEED_KMH = 35.0
DEFAULT_VISIT_MINUTES = 20

def format_location_target(r: Dict) -> str:
    name = (r.get("name") or "").strip()
    address = (r.get("address") or "").strip()
    nav_address = (r.get("navigation_address") or "").strip()

    # If this stop is part of a multi-merchant complex (mall/plaza with multiple stops),
    # use the shared navigation address directly so waypoints collapse to the single complex entrance.
    if r.get("_is_multi_merchant_complex") and nav_address:
        target = nav_address
    else:
        # 1. Clean name: remove internal brackets/parentheses, e.g. (North York), (Yonge & Sheppard)
        clean_name = re.sub(r"\([^)]*\)", "", name).strip()
        clean_name = re.sub(r"\s*[/\\\\]\s*", " - ", clean_name).strip()
        clean_name = re.sub(r"\s+", " ", clean_name).strip()

        # 2. Clean address: remove any slashes or duplicate whitespace
        base_addr = nav_address or address
        clean_addr = re.sub(r"\s*[/\\\\]\s*", " - ", base_addr).strip()
        clean_addr = re.sub(r"\s+", " ", clean_addr).strip()

        if not clean_addr:
            target = clean_name or "Toronto, ON"
        elif clean_name.lower() in clean_addr.lower():
            target = clean_addr
        elif not clean_name:
            target = clean_addr
        else:
            target = f"{clean_name}, {clean_addr}"

    # Explicitly guarantee Canadian scope to prevent Google Maps from accidentally resolving to US places
    if "canada" not in target.lower():
        target = f"{target}, Canada"

    return target

def build_google_maps_slash_url(origin_address: str, stops: List[Dict]) -> str:
    """
    Builds the slash-concatenated Google Maps URL that bypasses the 10-stop limit.
    Enforces safe='' so that slashes are properly encoded and segments remain intact.
    Deduplicates consecutive identical navigation destinations to conserve waypoints.
    """
    clean_origin = re.sub(r"\s*[/\\\\]\s*", " - ", origin_address).strip()
    origin_str = urllib.parse.quote(clean_origin, safe="")
    stop_strs = []
    last_target = None
    for s in stops:
        target = format_location_target(s)
        # Deduplicate consecutive identical navigation destinations (e.g. stores in same mall)
        if target == last_target:
            continue
        stop_strs.append(urllib.parse.quote(target, safe=""))
        last_target = target
    
    path = "/".join([origin_str] + stop_strs)
    return f"https://www.google.com/maps/dir/{path}/"

class RouteGenerator:
    def __init__(
        self,
        origin_name: str,
        origin_address: str,
        origin_lat: float,
        origin_lng: float,
        departure_time_str: str = "09:30",
        visit_minutes: int = DEFAULT_VISIT_MINUTES
    ):
        self.origin_name = origin_name
        self.origin_address = origin_address or "Toronto, ON"
        self.origin_lat = origin_lat
        self.origin_lng = origin_lng
        self.departure_time_str = departure_time_str
        self.visit_minutes = visit_minutes

    def process_route(self, stops: List[Dict], visit_date_str: str = "") -> Tuple[List[Dict], str, List[Dict]]:
        """
        Processes 30 stops:
        1. Generates the master slash-concatenated URL bypassing the 10-stop limit.
        2. Calculates estimated arrival and departure timestamps.
        3. Generates 4 optional segmented legs for mobile convenience.
        """
        if not stops:
            return [], "", []

        stops = cluster_navigation_addresses(stops)

        if not visit_date_str:
            visit_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        dep_dt = datetime.strptime(f"{visit_date_str} {self.departure_time_str}", "%Y-%m-%d %H:%M")
        cur_time = dep_dt
        cur_lat = self.origin_lat
        cur_lng = self.origin_lng

        # Avoid using fictitious business labels like 'Field Operations Base' or 'Coords (...)' in Google Maps geocoding
        clean_name = re.sub(r"\s*[/\\\\]\s*", " - ", self.origin_name or "").strip()
        clean_addr = re.sub(r"\s*[/\\\\]\s*", " - ", self.origin_address or "").strip()
        fictitious_names = {"field operations base", "default origin", "base", "origin"}

        if clean_name.lower() in fictitious_names or clean_name.lower().startswith("coords") or clean_name.lower().startswith("pin"):
            full_origin_str = clean_addr or f"{self.origin_lat:.6f},{self.origin_lng:.6f}"
        elif clean_name.lower() in clean_addr.lower():
            full_origin_str = clean_addr
        else:
            full_origin_str = f"{clean_name}, {clean_addr}" if clean_addr else clean_name

        if "canada" not in full_origin_str.lower() and not re.match(r"^[+-]?\d+\.\d+,[+-]?\d+\.\d+$", full_origin_str):
            full_origin_str = f"{full_origin_str}, Canada"

        # 1. Generate full 30-stop slash URL bypassing 10-stop limit (with deduplicated destinations)
        master_slash_url = build_google_maps_slash_url(full_origin_str, stops)

        processed_stops = []
        for idx, stop in enumerate(stops):
            s_lat = float(stop.get("latitude") or cur_lat)
            s_lng = float(stop.get("longitude") or cur_lng)

            # If consecutive stops share the exact navigation address, drive distance is 0 km and drive time is 2-min walking buffer
            is_same_nav = (
                idx > 0
                and stop.get("navigation_address")
                and stop.get("navigation_address") == processed_stops[idx - 1].get("navigation_address")
            )
            if is_same_nav:
                leg_dist = 0.0
                drive_mins = 2
            else:
                leg_dist = haversine_distance_km(cur_lat, cur_lng, s_lat, s_lng)
                drive_mins = max(5, round((leg_dist / AVERAGE_URBAN_SPEED_KMH) * 60))

            arrival_time = cur_time + timedelta(minutes=drive_mins)
            depart_time = arrival_time + timedelta(minutes=self.visit_minutes)

            leg_idx = (idx // MAX_WAYPOINTS_PER_LEG) + 1

            item = dict(stop)
            item["route_order"] = idx + 1
            item["leg_index"] = leg_idx
            item["est_arrival"] = arrival_time.strftime("%H:%M")
            item["est_departure"] = depart_time.strftime("%H:%M")
            item["leg_distance_km"] = round(leg_dist, 2)
            item["master_slash_url"] = master_slash_url
            processed_stops.append(item)

            cur_time = depart_time
            cur_lat = s_lat
            cur_lng = s_lng

        # 2. Build segmented legs (for mobile app tapping)
        legs = []
        total_stops = len(processed_stops)
        num_legs = math.ceil(total_stops / MAX_WAYPOINTS_PER_LEG)

        for leg_i in range(num_legs):
            start_i = leg_i * MAX_WAYPOINTS_PER_LEG
            end_i = min(start_i + MAX_WAYPOINTS_PER_LEG, total_stops)
            chunk = processed_stops[start_i:end_i]

            if leg_i == 0:
                leg_origin = full_origin_str
                leg_origin_coord = f"{self.origin_lat:.6f},{self.origin_lng:.6f}"
                from_label = f"Origin ({self.origin_name})"
            else:
                prev_stop = processed_stops[start_i - 1]
                leg_origin = format_location_target(prev_stop)
                prev_lat = float(prev_stop.get("latitude") or 0.0)
                prev_lng = float(prev_stop.get("longitude") or 0.0)
                leg_origin_coord = f"{prev_lat:.6f},{prev_lng:.6f}"
                from_label = f"Stop #{start_i} ({prev_stop.get('name')})"

            leg_url = build_google_maps_slash_url(leg_origin, chunk)

            # Build 100% unambiguous GPS coordinates URL (bypasses all text-matching and deduplicates same-complex stops)
            coord_strs = [leg_origin_coord]
            last_coord = leg_origin_coord
            last_nav = None
            for s in chunk:
                s_lat = float(s.get("latitude") or 0.0)
                s_lng = float(s.get("longitude") or 0.0)
                c_str = f"{s_lat:.6f},{s_lng:.6f}"
                nav_str = s.get("navigation_address") or c_str
                if nav_str != last_nav and c_str != last_coord:
                    coord_strs.append(c_str)
                    last_coord = c_str
                    last_nav = nav_str
            leg_gps_url = f"https://www.google.com/maps/dir/{'/'.join(coord_strs)}/"

            to_label = f"Stop #{end_i} ({chunk[-1].get('name')})"

            legs.append({
                "leg_number": leg_i + 1,
                "title": f"Leg {leg_i + 1}/{num_legs}: {from_label} -> {to_label}",
                "from": from_label,
                "to": to_label,
                "stops_count": len(chunk),
                "stop_range": f"{start_i + 1} - {end_i}",
                "url": leg_url,
                "gps_url": leg_gps_url,
                "stops_summary": " -> ".join([s.get("name", "") for s in chunk])
            })

        return processed_stops, master_slash_url, legs
