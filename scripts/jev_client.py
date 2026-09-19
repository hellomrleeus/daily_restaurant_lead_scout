#!/usr/bin/env python3
"""
TypeSafe AI Jev Decision Client for Daily Restaurant Lead Scout.
Interfaces with TypeSafe AI System One API (POST https://api.typesafe.ai/v1/systemone)
using the typed decision model `jev-latest`.

Philosophy:
"The model answers narrow, typed questions about the state. Your code owns the workflow."

Provides:
1. Low-level `query_decisions(state, questions)`
2. High-level `evaluate_entity_match(candidate, reference)`
3. High-level `evaluate_fried_food(place)`
4. Graceful fallback when API key is unconfigured or request fails.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple, List

DEFAULT_JEV_MODEL = "jev-latest"
TYPESAFE_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_DECISIONS_URL = TYPESAFE_SYSTEMONE_URL  # Backward compatibility alias

class JevFriedResult(tuple):
    """
    Backwards-compatible tuple that unpacks as 4 elements:
    (is_fried, conf, dishes, rationale)
    while exposing .tier_status and named properties for cascaded decisions.
    """
    def __new__(cls, is_fried, conf, dishes, rationale, tier_status="DEFINITIVE_PASS"):
        return super(JevFriedResult, cls).__new__(cls, (is_fried, conf, dishes, rationale))

    def __init__(self, is_fried, conf, dishes, rationale, tier_status="DEFINITIVE_PASS"):
        self.is_fried = is_fried
        self.confidence = conf
        self.dishes = dishes
        self.rationale = rationale
        self.tier_status = tier_status

    def __repr__(self):
        return (
            f"JevFriedResult(is_fried={self.is_fried}, conf={self.confidence:.2f}, "
            f"dishes={self.dishes}, tier_status='{self.tier_status}')"
        )

def classify_jev_tier(noul_val: float, cat_choice: str, oil_score: int) -> str:
    """
    Classifies candidate into one of three decision tiers:
    1. DEFINITIVE_PASS: Very high confidence positive (>=0.85 or strong fried category + oil tier 2).
    2. DEFINITIVE_REJECT: Very low confidence (<=0.30 or non_fried category with 0 oil).
    3. NEED_MULTIMODAL_INSPECTION: Borderline / ambiguous (0.30 < P < 0.85), needs vision inspection.
    """
    if (noul_val >= 0.85 and cat_choice != "non_fried") or (
        cat_choice in ("fried_chicken", "fast_food_burgers", "fish_and_chips")
        and oil_score == 2
        and noul_val >= 0.70
    ):
        return "DEFINITIVE_PASS"
    elif noul_val <= 0.30 or (cat_choice == "non_fried" and oil_score == 0 and noul_val <= 0.40):
        return "DEFINITIVE_REJECT"
    else:
        return "NEED_MULTIMODAL_INSPECTION"

class JevDecisionClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_JEV_MODEL,
        api_url: str = TYPESAFE_SYSTEMONE_URL,
        site_url: str = "https://github.com/hellomrleeus/daily_restaurant_lead_scout",
        site_name: str = "Daily Restaurant Lead Scout",
        timeout: float = 10.0
    ):
        self.api_key = (
            api_key
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or ""
        ).strip()
        self.model = model or DEFAULT_JEV_MODEL
        self.api_url = (
            os.environ.get("TYPESAFE_API_URL")
            or os.environ.get("JEV_API_URL")
            or api_url
            or TYPESAFE_SYSTEMONE_URL
        )
        self.site_url = site_url
        self.site_name = site_name
        self.timeout = timeout

    def is_ready(self) -> bool:
        """Returns True if a valid-looking TypeSafe / Jev API Key is configured."""
        return bool(self.api_key and not self.api_key.startswith("YOUR_") and len(self.api_key) > 8)

    def query_decisions(
        self,
        state: str,
        questions: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a typed decision request against TypeSafe AI System One endpoint.
        Returns the parsed 'answers' dictionary, or None if unavailable/failed.
        """
        if not self.is_ready():
            return None

        payload = {
            "model": self.model,
            "state": str(state).strip(),
            "questions": questions
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=req_data,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if isinstance(resp_data, dict):
                    return resp_data.get("answers", resp_data)
                return None
        except urllib.error.HTTPError as e:
            # Silently log and gracefully return None for pipeline continuity
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                err_body = str(e)
            print(f"  [Notice] TypeSafe Jev HTTP {e.code} warning: {err_body[:120]}")
            return None
        except Exception as e:
            print(f"  [Notice] TypeSafe Jev network/parse warning: {e}")
            return None

    def evaluate_entity_match(
        self,
        candidate: Dict[str, Any],
        reference: Dict[str, Any]
    ) -> Optional[Tuple[bool, float, str, str]]:
        """
        Uses Jev to determine whether a candidate restaurant matches an excluded reference merchant.
        Returns: (is_match, confidence, rationale, match_type) or None if Jev unavailable.
        """
        c_name = candidate.get("name", "")
        c_addr = candidate.get("address", "")
        c_phone = candidate.get("phone", "")
        c_type = candidate.get("primaryType", "")

        r_name = reference.get("name", "")
        r_addr = reference.get("address", "")
        r_phone = reference.get("phone", "")

        state = (
            f"Candidate Restaurant: Name='{c_name}', Address='{c_addr}', Phone='{c_phone}', Type='{c_type}'.\n"
            f"Reference Exclusion Merchant: Name='{r_name}', Address='{r_addr}', Phone='{r_phone}'."
        )

        questions = {
            "is_same_entity": {
                "type": "noul",
                "instructions": "Is the candidate restaurant the exact same physical store, branch, or business entity as the reference merchant?",
                "criteria": {
                    "true": "Exact same physical business and location, or same branch accounting for name variations / translations",
                    "false": "Different location, different branch of a chain, or unrelated business"
                }
            },
            "relationship": {
                "type": "choice",
                "instructions": "What is the structural relationship between candidate and reference?",
                "criteria": {
                    "same_store": "Exact same physical store/location",
                    "chain_different_branch": "Same brand/franchise chain, but at a different address",
                    "different_business": "Completely different businesses"
                }
            }
        }

        answers = self.query_decisions(state, questions)
        if not answers:
            return None

        noul_val = float((answers.get("is_same_entity") or {}).get("noul", 0.0))
        rel_choice = str((answers.get("relationship") or {}).get("choice", "different_business"))

        # Decision threshold:
        # If Jev says it is the exact same store with high confidence (> 0.70)
        # OR relationship is same_store and noul > 0.50 -> match!
        # If it's a chain but different branch, it should NOT be excluded!
        is_matched = (rel_choice == "same_store" and noul_val >= 0.50) or (noul_val >= 0.75)
        conf = noul_val
        rationale = f"Jev entity evaluation: relationship '{rel_choice}' (same-store probability {int(conf * 100)}%)"

        return is_matched, conf, rationale, rel_choice

    def evaluate_fried_food(
        self,
        place: Dict[str, Any]
    ) -> Optional[Tuple[bool, float, List[str], str]]:
        """
        Uses Jev to evaluate whether a restaurant actively sells fried food and operates fryers
        based on its name, cuisine category, editorial summary, and customer reviews text.
        Returns: (is_fried, confidence, dishes, rationale) or None if Jev unavailable.
        """
        name = place.get("name", "")
        primary_type = place.get("primaryType", "")
        categories = ", ".join(place.get("categories", []))
        summary = place.get("editorialSummary") or place.get("generativeSummary", "")
        reviews = place.get("reviews_text", "")

        state = (
            f"Restaurant Name: {name}\n"
            f"Primary Cuisine/Type: {primary_type}\n"
            f"Categories: {categories}\n"
            f"Editorial Summary: {summary}\n"
            f"Customer Reviews Excerpt: {reviews[:300]}"
        )

        questions = {
            "has_commercial_fryer": {
                "type": "noul",
                "instructions": "Does this restaurant operate commercial deep fryers and actively sell deep-fried food (e.g. fried chicken, wings, french fries, fish and chips, katsu, tempura, donuts)?",
                "criteria": {
                    "true": "Core menu or popular items include deep-fried foods using commercial fryers",
                    "false": "No deep fryers expected (e.g. salad bar, bakery, pure cafe, raw sushi bar without tempura)"
                }
            },
            "fried_category": {
                "type": "choice",
                "instructions": "What is the primary fried food offering category of this merchant?",
                "criteria": {
                    "fried_chicken": "Fried chicken, wings, tenders, Korean fried chicken",
                    "fast_food_burgers": "Burgers, french fries, onion rings",
                    "fish_and_chips": "Fish and chips, fried seafood",
                    "asian_fried": "Tonkatsu, katsu, tempura, karaage, egg rolls",
                    "pub_and_grill": "Pub food, calamari, mozzarella sticks, poutine",
                    "non_fried": "Minimal or no fried food items"
                }
            },
            "oil_consumption_level": {
                "type": "score",
                "instructions": "What is the expected cooking oil usage intensity for this establishment?",
                "criteria": [
                    "Negligible/None",
                    "Moderate (occasional fried sides/appetizers)",
                    "Heavy (core business relies on commercial fryers)"
                ]
            }
        }

        answers = self.query_decisions(state, questions)
        if not answers:
            return None

        noul_val = float((answers.get("has_commercial_fryer") or {}).get("noul", 0.0))
        cat_choice = str((answers.get("fried_category") or {}).get("choice", "non_fried"))
        oil_score = int((answers.get("oil_consumption_level") or {}).get("score", 0))

        # Category friendly label in English
        cat_labels = {
            "fried_chicken": "Fried Chicken & Wings",
            "fast_food_burgers": "Burgers & Fries Fast Food",
            "fish_and_chips": "Fish & Chips / Seafood",
            "asian_fried": "Asian Fried (Katsu/Tempura)",
            "pub_and_grill": "Pub & Grill Fried Snacks",
            "non_fried": "Non-fried Primary"
        }
        category_label = cat_labels.get(cat_choice, cat_choice)

        tier_status = classify_jev_tier(noul_val, cat_choice, oil_score)
        if tier_status == "DEFINITIVE_PASS":
            is_fried = True
        elif tier_status == "DEFINITIVE_REJECT":
            is_fried = False
        else:
            is_fried = (noul_val >= 0.55 and cat_choice != "non_fried") or (oil_score >= 1)

        conf = noul_val
        dishes = [category_label]
        rationale = f"Jev decision: Fryer probability {int(conf * 100)}%, category: {category_label}, estimated oil usage tier: {oil_score}/2 [{tier_status}]"

        return JevFriedResult(is_fried, conf, dishes, rationale, tier_status=tier_status)

