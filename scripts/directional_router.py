#!/usr/bin/env python3
"""
Directional sweep router module.
Implements the core unidirectional deep search and anti-shuttle ("不要折返跑") algorithm:
Corridor Slicing & Local Cluster Forward Sweep.

Eliminates both longitudinal backtracking and lateral cross-corridor ping-ponging.
"""

import math
from typing import List, Dict, Tuple, Optional

EARTH_RADIUS_KM = 6371.0

def haversine_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c

def get_bearing_unit_vector(bearing_degrees: float) -> Tuple[float, float]:
    """Returns (u_x, u_y) where u_x is East component and u_y is North component."""
    rad = math.radians(bearing_degrees)
    u_x = math.sin(rad) # East
    u_y = math.cos(rad) # North
    return u_x, u_y

def project_to_corridor(
    lat: float,
    lng: float,
    origin_lat: float,
    origin_lng: float,
    u_x: float,
    u_y: float
) -> Tuple[float, float]:
    """
    Projects (lat, lng) onto corridor travel axis:
    s: along-track forward progress distance (km)
    w: cross-track perpendicular lateral deviation (km)
    """
    mean_lat_rad = math.radians(origin_lat)
    dx_km = (lng - origin_lng) * (math.pi / 180.0) * EARTH_RADIUS_KM * math.cos(mean_lat_rad)
    dy_km = (lat - origin_lat) * (math.pi / 180.0) * EARTH_RADIUS_KM

    s = dx_km * u_x + dy_km * u_y
    w = abs(dx_km * (-u_y) + dy_km * u_x)

    return s, w

def calculate_destination_coords(origin_lat: float, origin_lng: float, distance_km: float, bearing_degrees: float) -> Tuple[float, float]:
    """Returns (lat, lng) after travelling distance_km along bearing_degrees."""
    rad_bearing = math.radians(bearing_degrees)
    lat1 = math.radians(origin_lat)
    lng1 = math.radians(origin_lng)
    dr = distance_km / EARTH_RADIUS_KM

    lat2 = math.asin(math.sin(lat1) * math.cos(dr) +
                     math.cos(lat1) * math.sin(dr) * math.cos(rad_bearing))
    lng2 = lng1 + math.atan2(
        math.sin(rad_bearing) * math.sin(dr) * math.cos(lat1),
        math.cos(dr) - math.sin(lat1) * math.sin(lat2)
    )

    return math.degrees(lat2), math.degrees(lng2)

def generate_corridor_probe_points(
    origin_lat: float,
    origin_lng: float,
    bearing_degrees: float,
    step_km: float = 3.5,
    max_depth_km: float = 25.0
) -> List[Tuple[float, float]]:
    """Generates consecutive probe centers along the travel axis."""
    probes = [(origin_lat, origin_lng)]
    cur_dist = step_km
    while cur_dist <= max_depth_km:
        plat, plng = calculate_destination_coords(origin_lat, origin_lng, cur_dist, bearing_degrees)
        probes.append((plat, plng))
        cur_dist += step_km
    return probes

def generate_radial_probe_points(
    origin_lat: float,
    origin_lng: float,
    radius_km: float = 6.0
) -> List[Tuple[float, float]]:
    """Generates a radial cluster of probe points centered at origin for proximity searches."""
    probes = [(origin_lat, origin_lng)]
    step = max(0.4, radius_km * 0.55)
    for b in [0, 45, 90, 135, 180, 225, 270, 315]:
        probes.append(calculate_destination_coords(origin_lat, origin_lng, step, b))
    return probes

def two_opt_tour(route: List[Dict], origin_lat: float, origin_lng: float, preserve_slices: bool = True) -> List[Dict]:
    """
    2-Opt local search refinement:
    Iteratively swaps pairs of edges to untangle crossing paths and minimize total route distance.
    If preserve_slices is True, prevents backward jumps across distant slices (maintaining anti-shuttle integrity).
    """
    if len(route) < 4:
        return route

    def calc_dist(tour: List[Dict]) -> float:
        d = 0.0
        c_lat, c_lng = origin_lat, origin_lng
        for item in tour:
            lat = float(item["latitude"])
            lng = float(item["longitude"])
            d += haversine_distance_km(c_lat, c_lng, lat, lng)
            c_lat, c_lng = lat, lng
        return d

    best_tour = list(route)
    best_dist = calc_dist(best_tour)
    improved = True
    iterations = 0
    max_iterations = 40

    while improved and iterations < max_iterations:
        improved = False
        iterations += 1
        for i in range(len(best_tour) - 1):
            for k in range(i + 1, len(best_tour)):
                if preserve_slices:
                    s_first = best_tour[i].get("_along_track_km")
                    s_last = best_tour[k].get("_along_track_km")
                    if s_first is not None and s_last is not None:
                        if abs(s_last - s_first) > 3.0:
                            continue

                candidate = best_tour[:i] + best_tour[i:k + 1][::-1] + best_tour[k + 1:]
                c_dist = calc_dist(candidate)
                if c_dist < best_dist - 1e-4:
                    best_tour = candidate
                    best_dist = c_dist
                    improved = True
                    break
            if improved:
                break

    return best_tour

class DirectionalRouter:
    def __init__(
        self,
        origin: Dict,
        bearing_degrees: float,
        corridor_width_km: float = 4.5,
        max_depth_km: float = 28.0,
        slice_length_km: float = 2.0
    ):
        self.origin = origin
        self.origin_lat = float(origin.get("latitude") or origin.get("lat", 43.7615))
        self.origin_lng = float(origin.get("longitude") or origin.get("lng", -79.4111))
        self.bearing_degrees = bearing_degrees
        self.u_x, self.u_y = get_bearing_unit_vector(bearing_degrees)
        self.corridor_width_km = corridor_width_km
        self.max_depth_km = max_depth_km
        self.slice_length_km = slice_length_km

    def filter_corridor_candidates(self, restaurants: List[Dict]) -> List[Dict]:
        """Filters restaurants to only those within the forward travel corridor."""
        corridor_list = []
        for r in restaurants:
            lat = float(r.get("latitude") or 0)
            lng = float(r.get("longitude") or 0)
            if lat == 0 or lng == 0:
                continue

            s, w = project_to_corridor(lat, lng, self.origin_lat, self.origin_lng, self.u_x, self.u_y)

            # Strictly forward (s >= 0) and within lateral corridor width, OR if user pinned it
            if r.get("_is_pinned") or (s >= -0.1 and s <= self.max_depth_km and w <= self.corridor_width_km):
                r_copy = dict(r)
                r_copy["_along_track_km"] = round(s, 2)
                r_copy["_cross_track_km"] = round(w, 2)
                corridor_list.append(r_copy)

        return corridor_list

    def plan_unidirectional_route(self, restaurants: List[Dict], target_count: int = 30) -> List[Dict]:
        """
        Anti-Shuttle ("不要折返跑") Corridor Slice & Cluster Sweep:
        1. Guarantees inclusion of any user-pinned mandatory stops.
        2. Bins candidates into monotonic forward depth slices [0..slice_len], [slice_len..2*slice_len], etc.
        3. In each slice, visits local candidates via compact nearest-neighbor tour.
        4. Once a slice is cleared, proceeds strictly forward to the next slice with zero backward shuttling.
        """
        candidates = self.filter_corridor_candidates(restaurants)

        # Expand corridor slightly if insufficient candidates
        if len(candidates) < target_count:
            relaxed_width = self.corridor_width_km * 1.6
            candidates = []
            for r in restaurants:
                lat = float(r.get("latitude") or 0)
                lng = float(r.get("longitude") or 0)
                if lat == 0 or lng == 0:
                    continue
                s, w = project_to_corridor(lat, lng, self.origin_lat, self.origin_lng, self.u_x, self.u_y)
                if r.get("_is_pinned") or (s >= -0.3 and s <= (self.max_depth_km * 1.3) and w <= relaxed_width):
                    r_copy = dict(r)
                    r_copy["_along_track_km"] = round(s, 2)
                    r_copy["_cross_track_km"] = round(w, 2)
                    candidates.append(r_copy)

        if not candidates:
            return []

        # If candidates exceed target_count, prioritize retaining any pinned restaurants
        if len(candidates) > target_count:
            pinned = [c for c in candidates if c.get("_is_pinned")]
            unpinned = [c for c in candidates if not c.get("_is_pinned")]
            unpinned.sort(key=lambda x: max(0.0, x.get("_along_track_km", 0)))
            needed_unpinned = max(0, target_count - len(pinned))
            candidates = pinned + unpinned[:needed_unpinned]

        # Sort all candidates into forward depth slices
        # Slice index k covers: [k * slice_length, (k + 1) * slice_length)
        slices: Dict[int, List[Dict]] = {}
        for c in candidates:
            s_val = max(0.0, c["_along_track_km"])
            slice_idx = int(s_val // self.slice_length_km)
            slices.setdefault(slice_idx, []).append(c)

        ordered_route: List[Dict] = []
        cur_lat = self.origin_lat
        cur_lng = self.origin_lng

        # Sweep forward slice by slice (monotonic forward order: slice 0, 1, 2, ...)
        for slice_idx in sorted(slices.keys()):
            if len(ordered_route) >= target_count:
                break

            slice_items = slices[slice_idx]
            # Inside the slice: perform local nearest-neighbor sweep
            while slice_items and len(ordered_route) < target_count:
                best_i = 0
                min_dist = float("inf")
                for i, item in enumerate(slice_items):
                    d = haversine_distance_km(cur_lat, cur_lng, float(item["latitude"]), float(item["longitude"]))
                    if d < min_dist:
                        min_dist = d
                        best_i = i

                chosen = slice_items.pop(best_i)
                ordered_route.append(chosen)
                cur_lat = float(chosen["latitude"])
                cur_lng = float(chosen["longitude"])

        return two_opt_tour(ordered_route[:target_count], self.origin_lat, self.origin_lng, preserve_slices=True)

    def plan_radial_route(self, restaurants: List[Dict], target_count: int = 30) -> List[Dict]:
        """
        Plans a compact cluster route around the anchor origin for '在XXXX附近找' scenarios.
        Filters by proximity to origin, guarantees pinned stops, then chains nearest neighbors outwards smoothly.
        """
        candidates = []
        for r in restaurants:
            lat = float(r.get("latitude") or 0)
            lng = float(r.get("longitude") or 0)
            if lat == 0 or lng == 0:
                continue
            d = haversine_distance_km(self.origin_lat, self.origin_lng, lat, lng)
            if r.get("_is_pinned") or (d <= self.max_depth_km * 1.3):
                r_copy = dict(r)
                r_copy["_dist_from_origin_km"] = round(d, 2)
                candidates.append(r_copy)

        if not candidates:
            return []

        # If candidates exceed target_count, ensure all pinned stops are preserved
        if len(candidates) > target_count:
            pinned = [c for c in candidates if c.get("_is_pinned")]
            unpinned = [c for c in candidates if not c.get("_is_pinned")]
            unpinned.sort(key=lambda x: x.get("_dist_from_origin_km", 0))
            needed_unpinned = max(0, target_count - len(pinned))
            candidates = pinned + unpinned[:needed_unpinned]

        ordered_route: List[Dict] = []
        cur_lat = self.origin_lat
        cur_lng = self.origin_lng

        while candidates and len(ordered_route) < target_count:
            best_i = 0
            min_dist = float("inf")
            for i, item in enumerate(candidates):
                d = haversine_distance_km(cur_lat, cur_lng, float(item["latitude"]), float(item["longitude"]))
                if d < min_dist:
                    min_dist = d
                    best_i = i

            chosen = candidates.pop(best_i)
            ordered_route.append(chosen)
            cur_lat = float(chosen["latitude"])
            cur_lng = float(chosen["longitude"])

        return two_opt_tour(ordered_route[:target_count], self.origin_lat, self.origin_lng, preserve_slices=False)
