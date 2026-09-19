#!/usr/bin/env python3
"""
Agent-Native Multimodal Auditor for Fried Food Detection.
Zero External Vision API Dependency.

Architecture (Cascaded Active Multimodal Evaluation):
1. Tier 1: Jev Decision Client (~typesafe/jev-latest) performs fast text triage.
   - High confidence positive (P >= 0.85): instant pass (no image needed).
   - High confidence negative (P <= 0.30): instant reject (no image needed).
2. Tier 2: For ambiguous / borderline candidates (0.30 < P < 0.85):
   - Photos from Google Places are downloaded to the system temporary directory.
   - Ambiguous candidates are exported to pending_agent_audit.json with local photo paths.
   - The executing AI Agent (Antigravity / Gemini) visually inspects the photos using native view_file.
   - Verified decisions in agent_audit_results.json take absolute precedence.
3. Tier 3: Local culinary NLP heuristic for autonomous offline fallbacks.
"""

import os
import re
import json
import urllib.request
import tempfile
from typing import Dict, List, Tuple, Optional, Any

try:
    from config_manager import get_temp_dir
except ImportError:
    def get_temp_dir(subfolder: str = "audit") -> str:
        d = os.path.join(tempfile.gettempdir(), "restaurant_lead_scout", subfolder)
        os.makedirs(d, exist_ok=True)
        return d

FRIED_DISH_PATTERNS = [
    "french fries", "fries", "chips", "fried chicken", "wings", "buffalo wings",
    "chicken wings", "tenders", "nuggets", "popcorn chicken", "korean fried chicken",
    "tonkatsu", "katsu", "tempura", "karaage", "fish and chips", "halibut and chips",
    "calamari", "fried squid", "onion rings", "corn dog", "rice dog", "churros",
    "funnel cake", "doughnuts", "donuts", "spring rolls", "egg rolls", "wontons",
    "samosa", "samosas", "falafel", "schnitzel", "empanadas", "chimichanga",
    "fried rice", "tater tots", "hushpuppies", "fried pickles", "poutine",
    "salt and pepper chicken", "salt & pepper squid", "deep fried"
]

try:
    from jev_client import JevDecisionClient, classify_jev_tier, JevFriedResult
except ImportError:
    JevDecisionClient = None
    classify_jev_tier = None
    JevFriedResult = None

def download_candidate_photo(url: str, dest_path: str, timeout: float = 6.0) -> bool:
    """Downloads a photo from a URL to a local destination file."""
    if not url or not dest_path:
        return False
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
        return True
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (DailyRestaurantLeadScout/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) > 500:
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
    except Exception:
        pass
    return False

class AgentAuditManager:
    def __init__(
        self,
        audit_dir: Optional[str] = None,
        typesafe_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        jev_client: Optional[Any] = None,
        **kwargs
    ):
        self.audit_dir = audit_dir or get_temp_dir("audit")
        self.photos_dir = os.path.join(self.audit_dir, "photos")
        os.makedirs(self.photos_dir, exist_ok=True)

        self.pending_file = os.path.join(self.audit_dir, "pending_agent_audit.json")
        self.results_file = os.path.join(self.audit_dir, "agent_audit_results.json")
        self.agent_decisions = {}
        self.ambiguous_candidates = []
        self._load_agent_decisions()

        # Audit breakdown stats for reporting
        self.stats = {
            "total": 0,
            "agent_cached": 0,
            "jev_pass": 0,
            "jev_reject": 0,
            "ambiguous_need_agent": 0,
            "heuristic": 0
        }

        # Initialize Jev Decision Client
        self.jev_client = jev_client
        candidate_key = (
            typesafe_api_key
            or kwargs.get("typesafe_key")
            or kwargs.get("jev_key")
            or openrouter_api_key
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        if self.jev_client is None and JevDecisionClient is not None:
            if candidate_key:
                self.jev_client = JevDecisionClient(api_key=candidate_key)
            else:
                default_client = JevDecisionClient()
                if default_client.is_ready():
                    self.jev_client = default_client

    def _load_agent_decisions(self):
        """Loads agent-verified multimodal decisions from agent_audit_results.json."""
        if os.path.exists(self.results_file):
            try:
                with open(self.results_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            pid = item.get("placeId") or item.get("id")
                            if pid:
                                self.agent_decisions[pid] = item
                    elif isinstance(data, dict):
                        self.agent_decisions = data
                print(f"  [Info] Loaded {len(self.agent_decisions)} agent-verified multimodal decisions from {self.results_file}")
            except Exception as e:
                print(f"  [Notice] Note: Could not parse {self.results_file}: {e}")

    def export_pending_audit(
        self,
        candidates: List[Dict],
        download_images: bool = True
    ) -> Tuple[str, List[Dict]]:
        """
        Filters candidates to find ONLY ambiguous / borderline candidates needing
        Multimodal Agent visual verification (P between 0.30 and 0.85).
        Downloads food photos to local disk so the Agent can inspect them with view_file.
        """
        os.makedirs(self.photos_dir, exist_ok=True)
        ambiguous_items = []

        for c in candidates:
            pid = c.get("placeId") or c.get("id", "")
            if not pid:
                continue

            # Skip if already verified by agent
            if pid in self.agent_decisions:
                continue

            # Check Jev status
            needs_audit = False
            jev_conf = 0.5
            jev_rationale = ""

            if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
                jev_eval = self.jev_client.evaluate_fried_food(c)
                if jev_eval is not None:
                    _, conf, _, rat = jev_eval[:4]
                    tier_status = getattr(jev_eval, "tier_status", None)
                    if not tier_status and len(jev_eval) >= 5:
                        tier_status = jev_eval[4]

                    jev_conf = conf
                    jev_rationale = rat
                    if tier_status == "NEED_MULTIMODAL_INSPECTION" or (tier_status is None and 0.30 < conf < 0.85):
                        needs_audit = True
            else:
                # If no Jev, check if primary type is borderline (e.g. japanese, asian, bistro, bar)
                ptype = (c.get("primaryType") or "").lower()
                borderline_types = ["japanese", "ramen", "izakaya", "asian", "chinese", "bistro", "tapas", "bar", "cafe", "bakery"]
                if any(bt in ptype for bt in borderline_types):
                    needs_audit = True

            if needs_audit:
                # Download up to 2 photos locally for agent inspection
                photo_urls = c.get("photo_urls", [])
                local_paths = []
                if download_images and photo_urls:
                    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", pid)[-24:]
                    for idx, purl in enumerate(photo_urls[:2]):
                        dest = os.path.join(self.photos_dir, f"{clean_id}_{idx}.jpg")
                        if download_candidate_photo(purl, dest):
                            local_paths.append(dest)

                ambiguous_items.append({
                    "placeId": pid,
                    "name": c.get("name"),
                    "primaryType": c.get("primaryType"),
                    "address": c.get("address"),
                    "jev_confidence": round(jev_conf, 2),
                    "jev_rationale": jev_rationale,
                    "local_photo_paths": local_paths,
                    "photo_urls": photo_urls,
                    "editorialSummary": c.get("editorialSummary", ""),
                    "reviews_excerpt": c.get("reviews_text", "")
                })

        self.ambiguous_candidates = ambiguous_items
        with open(self.pending_file, "w", encoding="utf-8") as f:
            json.dump(ambiguous_items, f, ensure_ascii=False, indent=2)

        return self.pending_file

    def audit_restaurant(self, place: Dict) -> Tuple[bool, float, List[str], str]:
        """
        Audits a restaurant using Cascaded Active Evaluation:
        1. Agent pre-verified multimodal decisions take absolute precedence.
        2. Tier 1: Jev Decision Model (~typesafe/jev-latest) for fast text classification.
           - If definitive pass (>=0.85): instant pass, no image needed.
           - If definitive reject (<=0.30): instant reject, no image needed.
           - If borderline (0.30 < P < 0.85): marked as ambiguous, uses agent decision if available.
        3. Tier 3: Local high-recall heuristic fallback.
        """
        self.stats["total"] += 1
        pid = place.get("placeId") or place.get("id", "")

        # 1. Agent multimodal decision has highest priority
        if pid in self.agent_decisions:
            self.stats["agent_cached"] += 1
            decision = self.agent_decisions[pid]
            is_fried = bool(decision.get("is_fried", True))
            dishes = decision.get("fried_dishes") or decision.get("dishes") or ["agent-multimodal-verified"]
            rationale = decision.get("rationale") or decision.get("notes") or "Verified by AI Agent multimodal image inspection"
            return is_fried, 1.0, dishes, rationale

        # 2. TypeSafe Jev Tiered Decision Model
        if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
            jev_eval = self.jev_client.evaluate_fried_food(place)
            if jev_eval is not None:
                is_fried, conf, dishes, rationale = jev_eval[:4]
                tier_status = getattr(jev_eval, "tier_status", None)
                if not tier_status and len(jev_eval) >= 5:
                    tier_status = jev_eval[4]

                if tier_status == "DEFINITIVE_PASS":
                    self.stats["jev_pass"] += 1
                    return True, conf, dishes, rationale
                elif tier_status == "DEFINITIVE_REJECT":
                    self.stats["jev_reject"] += 1
                    return False, conf, dishes, rationale
                else:
                    # Ambiguous candidate
                    self.stats["ambiguous_need_agent"] += 1
                    amb_rationale = f"Jev borderline (Fryer prob {int(conf * 100)}%): Pending Agent multimodal image review"
                    return is_fried, conf, dishes, amb_rationale

        # 3. Local high-recall heuristic fallback
        self.stats["heuristic"] += 1
        return self._heuristic_audit(place)

    def _heuristic_audit(self, place: Dict) -> Tuple[bool, float, List[str], str]:
        name = place.get("name", "")
        primary_type = place.get("primaryType", "")
        summary = place.get("editorialSummary") or place.get("generativeSummary", "")
        categories = place.get("categories", [])
        reviews = place.get("reviews_text", "")

        haystack = f"{name} {primary_type} {summary} {' '.join(categories)} {reviews}".lower()
        matched = []

        for p in FRIED_DISH_PATTERNS:
            if p in haystack:
                matched.append(p)

        fryer_heavy_types = [
            "fast_food_restaurant", "hamburger_restaurant", "american_restaurant",
            "japanese_restaurant", "korean_restaurant", "chicken_restaurant",
            "seafood_restaurant", "bar_and_grill", "pub"
        ]
        is_fryer_type = any(t in primary_type.lower() for t in fryer_heavy_types)

        if matched:
            return True, 0.95, list(set(matched)), f"Detected fried dishes in menu/reviews: {', '.join(matched[:3])}"
        elif is_fryer_type:
            return True, 0.80, ["commercial fryer expected"], f"Category ({primary_type}) standardly operates commercial fryers"
        else:
            return False, 0.30, [], "No fried food items identified"
