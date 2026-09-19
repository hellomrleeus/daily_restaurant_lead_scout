#!/usr/bin/env python3
"""
Places searcher module using Google Places API (New).
Directly communicates with https://places.googleapis.com/v1/places:searchText in pure English.
"""

import os
import json
import math
import time
import gzip
import http.client
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Tuple
from opening_hours import format_weekday_opening_hours

FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "places.websiteUri,"
    "places.googleMapsUri,"
    "places.rating,"
    "places.userRatingCount,"
    "places.regularOpeningHours,"
    "places.currentOpeningHours,"
    "places.priceLevel,"
    "places.primaryType,"
    "places.location,"
    "places.photos,"
    "places.editorialSummary,"
    "places.reviews,"
    "places.businessStatus,"
    "nextPageToken"
)

PRICE_MAP = {
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$"
}

def strip_emojis(text: str) -> str:
    if not text:
        return ""
    clean_chars = []
    for ch in text:
        code = ord(ch)
        if code > 0x1F000 or (0x2600 <= code <= 0x27BF):
            continue
        clean_chars.append(ch)
    return "".join(clean_chars).strip()

def transform_google_place(p: Dict, matched_term: str = "", default_region: str = "GTA", api_key: str = "") -> Dict:
    disp = (p.get("displayName") or {}).get("text") if isinstance(p.get("displayName"), dict) else ""
    raw_name = p.get("name") if not (str(p.get("name", "")).startswith("places/")) else ""
    name = strip_emojis(disp or raw_name or p.get("name") or "Unnamed Restaurant")
    address = p.get("address") or p.get("formattedAddress") or ""
    address = strip_emojis(address.replace("加拿大", "Canada")).strip()
    phone = p.get("phone") or p.get("nationalPhoneNumber") or "None"
    website = p.get("website") or p.get("websiteUri") or ""
    place_id = p.get("placeId") or p.get("id") or ""
    maps_url = p.get("mapsUrl") or p.get("googleMapsUri") or f"https://www.google.com/maps/place/?q=place_id:{place_id}"
    
    rating = float(p.get("rating", 0.0)) if p.get("rating") else 0.0
    user_count = p.get("userRatingCount")
    if user_count is not None:
        try:
            reviews_count = int(user_count)
        except Exception:
            reviews_count = 0
    elif isinstance(p.get("reviews"), list):
        reviews_count = len(p.get("reviews"))
    else:
        reviews_count = 0
    primary_type = p.get("primaryType") or "restaurant"
    
    lat = float(p.get("latitude") or (p.get("location") or {}).get("latitude", 0.0))
    lng = float(p.get("longitude") or (p.get("location") or {}).get("longitude", 0.0))

    # Opening hours: run the simplified algorithm
    reg_hours = p.get("regularOpeningHours") or {}
    weekday_desc = reg_hours.get("weekdayDescriptions") if isinstance(reg_hours, dict) else None
    raw_hours = weekday_desc or p.get("openingHours") or reg_hours
    opening_hours = format_weekday_opening_hours(raw_hours)

    raw_summary = (p.get("editorialSummary") or {}).get("text", "") if isinstance(p.get("editorialSummary"), dict) else ""
    editorial_summary = strip_emojis(raw_summary)
    
    reviews_val = p.get("reviews")
    reviews_text = ""
    if isinstance(reviews_val, list):
        reviews_text = strip_emojis(" ".join([r.get("text", {}).get("text", "") for r in reviews_val[:3] if isinstance(r, dict)]))

    # Collect photo URLs
    photos = p.get("photos", []) if isinstance(p.get("photos"), list) else []
    photo_urls = []
    if api_key:
        for ph in photos[:3]:
            ph_name = ph.get("name")
            if ph_name:
                photo_urls.append(f"https://places.googleapis.com/v1/{ph_name}/media?key={api_key}&maxHeightPx=600&maxWidthPx=600")

    # Determine region from address
    region = default_region
    addr_lower = address.lower()
    if "markham" in addr_lower:
        region = "Markham"
    elif "scarborough" in addr_lower:
        region = "Scarborough"
    elif "north york" in addr_lower:
        region = "North York"
    elif "richmond hill" in addr_lower:
        region = "Richmond Hill"
    elif "mississauga" in addr_lower:
        region = "Mississauga"
    elif "vaughan" in addr_lower:
        region = "Vaughan"
    elif "toronto" in addr_lower:
        region = "Downtown Toronto"

    business_status = (p.get("businessStatus") or p.get("business_status") or "OPERATIONAL").strip()

    return {
        "name": name,
        "region": region,
        "rating": rating,
        "reviews": reviews_count,
        "business_status": business_status,
        "openingHours": opening_hours,
        "rawOpeningHours": weekday_desc or p.get("openingHours"),
        "regularOpeningHours": reg_hours,
        "regular_opening_hours": reg_hours,
        "address": address,
        "phone": phone,
        "website": website,
        "mapsUrl": maps_url,
        "primaryType": primary_type,
        "editorialSummary": editorial_summary,
        "reviews_text": reviews_text,
        "photo_urls": photo_urls,
        "latitude": lat,
        "longitude": lng,
        "placeId": place_id
    }

class PlacesSearcher:
    def __init__(self, api_key: Optional[str] = None, referer: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        self.referer = (referer or os.environ.get("GOOGLE_MAPS_API_REFERER", "")).strip()

    def is_live_api_ready(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("YOUR_"))

    def search_places_api(self, text_query: str, lat: float, lng: float, radius_meters: int = 5000, page_token: str = "") -> Dict:
        if not self.is_live_api_ready():
            raise ValueError("Google Maps API Key 未配置或无效。请先配置有效的 API Key: python3 scripts/main.py --configure --set-api-key <KEY>")

        url = "https://places.googleapis.com/v1/places:searchText"
        payload = {
            "textQuery": text_query,
            "maxResultCount": 20,
            "languageCode": "en",
            "regionCode": "CA",
            "locationBias": {
                "circle": {
                    "center": {
                        "latitude": lat,
                        "longitude": lng
                    },
                    "radius": float(radius_meters)
                }
            }
        }
        if page_token:
            payload["pageToken"] = page_token

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Goog-Api-Key", self.api_key)
        if self.referer:
            req.add_header("Referer", self.referer)
        field_mask = (
            "places.id,places.displayName,places.formattedAddress,places.location,"
            "places.regularOpeningHours,places.currentOpeningHours,places.businessStatus,"
            "places.primaryType,places.editorialSummary,places.reviews,places.nationalPhoneNumber,"
            "places.internationalPhoneNumber,places.rating,places.userRatingCount,places.websiteUri,"
            "places.googleMapsUri,places.photos"
        )
        req.add_header("X-Goog-FieldMask", field_mask)
        req.add_header("Accept-Encoding", "gzip, deflate")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw_data = resp.read()
                    headers_obj = getattr(resp, "headers", None)
                    encoding = headers_obj.get("Content-Encoding", "") if headers_obj and hasattr(headers_obj, "get") else ""
                    if str(encoding).lower() == "gzip":
                        raw_data = gzip.decompress(raw_data)
                    return json.loads(raw_data.decode("utf-8"))
            except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError) as err:
                if attempt == max_retries - 1:
                    raise err
                time.sleep(0.8 * (attempt + 1))

    def search_corridor_probes(
        self,
        probe_points: List[Tuple[float, float]],
        keywords: List[str],
        radius_meters: int = 5000,
        max_per_probe: int = 40,
        target_count: int = 30
    ) -> List[Dict]:
        """
        Executes multi-hop corridor probing across a sequence of probe centers using Google Places API.
        Returns deduplicated candidate restaurants in pure English format.
        """
        if not self.is_live_api_ready():
            raise RuntimeError("Google Maps API Key 未配置或无效。请先配置有效的 API Key: python3 scripts/main.py --configure --set-api-key <KEY>")

        places_map = {}
        print(f"Querying Google Places API (New) across {len(probe_points)} corridor probes in English...")
        selected_keywords = keywords[:4] if keywords else ["fried chicken", "chicken wings", "fish and chips"]

        min_pool_target = max(100, target_count * 4)

        for idx, (p_lat, p_lng) in enumerate(probe_points):
            print(f"  Probe #{idx + 1}/{len(probe_points)} at ({p_lat:.4f}, {p_lng:.4f}) radius {radius_meters}m...")
            probe_new_count = 0

            for kw in selected_keywords:
                kw_clean = kw.strip()
                kw_lower = kw_clean.lower()
                has_suffix = any(term in kw_lower for term in ["restaurant", "food", "court", "dining", "kitchen", "cafe", "bakery", "eatery", "bar"])
                query = kw_clean if has_suffix else f"{kw_clean} restaurant"
                try:
                    data = self.search_places_api(query, p_lat, p_lng, radius_meters=radius_meters)
                    places = data.get("places", [])
                    for p in places:
                        pid = p.get("id")
                        if not pid:
                            continue
                        if pid not in places_map:
                            transformed = transform_google_place(p, matched_term=kw, api_key=self.api_key)
                            places_map[pid] = transformed
                            probe_new_count += 1
                except Exception as e:
                    print(f"    [Warning] API Query '{query}': {e}")

            print(f"  Probe #{idx + 1} found {probe_new_count} new candidates (Total pooled: {len(places_map)})")
            if len(places_map) >= min_pool_target and idx >= 3:
                print(f"  已在走廊内检索到充沛商户 ({len(places_map)} 家)，探测完毕。")
                break
            time.sleep(0.3)

        return list(places_map.values())
