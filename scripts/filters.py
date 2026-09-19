import json
import os
import re
from typing import List, Dict, Tuple, Set, Optional, Any

try:
    from opening_hours import check_restaurant_open_status
except ImportError:
    def check_restaurant_open_status(restaurant, visit_date_str="", departure_time_str="", allow_dinner_only=False, max_acceptable_open_hour=17):
        return True, "营业中"

try:
    from exclusion_loader import load_exclusion_source, normalize_raw_record
except ImportError:
    def load_exclusion_source(source: Optional[str]) -> List[Dict[str, Any]]:
        if not source or not os.path.exists(source):
            return []
        try:
            with open(source, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

def normalize_phone(phone: str) -> str:
    if not phone or phone in ("无", "未提供", "未知", "null", "None"):
        return ""
    digits = re.sub(r"\D", "", phone)
    # Remove leading country code 1 if length is 11
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits

def normalize_text(text: str) -> str:
    if not text:
        return ""
    # Lowercase, remove punctuation and extra spaces
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fa5]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()

def extract_brand_tokens(name: str) -> Set[str]:
    """Extracts distinctive brand words ignoring common restaurant generic words, food types, and city names."""
    stop_words = {
        # Generic venue & business words
        "restaurant", "bar", "kitchen", "grill", "inc", "ltd", "corp",
        "food", "foods", "express", "cafe", "takeout", "eatery", "house", "shop",
        "plaza", "centre", "center", "mall", "station", "unit", "hwy", "highway",
        "lounge", "bistro", "diner", "place", "original", "famous", "best",
        # Generic food & cuisine categories
        "chicken", "wings", "burger", "burgers", "hotdog", "hotdogs", "dog", "dogs",
        "tacos", "taco", "pizza", "sushi", "noodle", "noodles", "rice", "fried",
        "donkatsu", "tonkatsu", "katsu", "seafood", "fish", "chips", "pot", "tofu",
        "dumplings", "tea", "coffee", "bakery", "asian", "korean", "japanese",
        "chinese", "thai", "mexican", "halibut", "meals", "postpartum",
        # Region & geographic words
        "markham", "toronto", "scarborough", "york", "north", "south", "east", "west",
        "downtown", "richmond", "hill", "vaughan", "mississauga", "ontario", "canada", "gta",
        # Chinese stop words
        "餐馆", "餐厅", "美食", "店", "分店", "料理", "快餐", "小吃", "北约克", "士嘉堡", "万锦", "多伦多"
    }
    clean = name.lower()
    clean = re.sub(r"\bbb\.?q\b", "bbq", clean)
    clean = clean.replace("&", "and")
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fa5]+", clean)
    return {t for t in tokens if t not in stop_words and len(t) > 2}

def extract_address_features(address: str) -> Tuple[Optional[str], Set[str]]:
    """Extracts street number and distinctive road tokens."""
    if not address:
        return None, set()
    clean = address.lower().replace(",", " ")
    num_match = re.search(r"\b(\d{1,6})\b", clean)
    street_num = num_match.group(1) if num_match else None

    stop_addr = {
        "rd", "road", "st", "street", "ave", "avenue", "dr", "drive", "blvd",
        "unit", "suite", "hwy", "highway", "canada", "on", "north", "york",
        "toronto", "markham", "scarborough", "richmond", "hill", "mississauga"
    }
    tokens = re.findall(r"[a-z0-9]+", clean)
    keywords = {t for t in tokens if t not in stop_addr and len(t) > 2 and not t.isdigit()}
    return street_num, keywords

try:
    from jev_client import JevDecisionClient
except ImportError:
    JevDecisionClient = None

CHAIN_BRANDS = {
    "popeyes", "kfc", "bbq", "churchs", "jollibee", "mcdonalds",
    "wendys", "mary", "browns", "buffalo", "wild", "wings", "fry"
}

def semantic_entity_match(
    candidate: Dict,
    reference_items: List[Dict],
    jev_client: Optional[Any] = None
) -> Tuple[bool, float, str, Optional[Dict]]:
    """
    Tier 2: Model-driven semantic entity matching.
    Resolves variations in branch names, brand aliases, and spatial address overlaps.
    Accurately differentiates chain branches (requiring address consistency) from
    unique independent restaurants.
    Integrates TypeSafe AI Jev decision model (System One) with seamless heuristic fallback.
    Returns: (is_matched, confidence, rationale, matched_record)
    """
    c_name = candidate.get("name", "")
    c_addr = candidate.get("address", "")
    c_brand_tokens = extract_brand_tokens(c_name)
    c_num, c_addr_tokens = extract_address_features(c_addr)

    if not c_brand_tokens:
        return False, 0.0, "", None

    for ref in reference_items:
        r_name = ref.get("name", "")
        r_addr = ref.get("address", "")
        r_brand_tokens = extract_brand_tokens(r_name)

        common_brands = c_brand_tokens & r_brand_tokens
        if not common_brands:
            continue

        # 1. TypeSafe Jev Decision Model check (if configured and ready)
        if jev_client and getattr(jev_client, "is_ready", lambda: False)():
            jev_res = jev_client.evaluate_entity_match(candidate, ref)
            if jev_res is not None:
                is_m, conf, rationale, rel = jev_res
                if is_m:
                    return True, conf, f"Jev 决策: {rationale}", ref
                elif rel == "chain_different_branch":
                    # Same chain brand but distinct physical branch: proceed without false exclusion
                    continue

        # 2. Local heuristic rule fallback
        r_num, r_addr_tokens = extract_address_features(r_addr)
        shared_brand_name = list(common_brands)[0]
        is_chain = any(b in CHAIN_BRANDS for b in common_brands)

        # Case A: Same brand and exact same street number (Definite match)
        if c_num and r_num and c_num == r_num:
            addr_overlap = c_addr_tokens & r_addr_tokens
            if addr_overlap or not c_addr_tokens or not r_addr_tokens:
                return (
                    True,
                    0.98,
                    f"核心品牌 '{shared_brand_name}' 一致，且门牌号 '{c_num}' 相同",
                    ref
                )

        # Case B: Significant address road overlap (Same Plaza / Same Street branch)
        if c_addr_tokens and r_addr_tokens:
            common_road = c_addr_tokens & r_addr_tokens
            if common_road:
                return (
                    True,
                    0.93,
                    f"核心品牌 '{shared_brand_name}' 一致，且同在 '{list(common_road)[0]}' 商圈/路段",
                    ref
                )

        # Case C: Independent unique brands (e.g. Wohebaobei, Kate & Jan, Chungchun)
        if not is_chain:
            for b in common_brands:
                if len(b) >= 6 or b in ("chungchun", "wohebaobei", "banban", "kosam", "bullger", "akoya"):
                    return (
                        True,
                        0.89,
                        f"高特征独立品牌 '{b}' 实体高度对齐",
                        ref
                    )

    return False, 0.0, "", None

class RestaurantFilter:
    def __init__(
        self,
        contracted_path: Optional[str] = None,
        visited_path: Optional[str] = None,
        exclude_regions: Optional[List[str]] = None,
        include_regions: Optional[List[str]] = None,
        exclude_restaurants: Optional[List[str]] = None,
        mandatory_restaurants: Optional[List[str]] = None,
        visit_date: str = "",
        departure_time: str = "09:30",
        filter_closed: bool = True,
        allow_dinner_only: bool = False,
        typesafe_api_key: Optional[str] = None,
        openrouter_api_key: Optional[str] = None,
        jev_client: Optional[Any] = None,
        **kwargs
    ):
        self.contracted_path = contracted_path or ""
        self.visited_path = visited_path or ""
        self.contracted_data = load_exclusion_source(contracted_path) if contracted_path else []
        self.visited_data = load_exclusion_source(visited_path) if visited_path else []
        self.exclude_regions = [r.lower().strip() for r in (exclude_regions or []) if r and r.strip()]
        self.include_regions = [r.lower().strip() for r in (include_regions or []) if r and r.strip()]
        self.exclude_restaurants = [r.lower().strip() for r in (exclude_restaurants or []) if r and r.strip()]
        self.mandatory_restaurants = [r.lower().strip() for r in (mandatory_restaurants or []) if r and r.strip()]
        self.visit_date = visit_date
        self.departure_time = departure_time
        self.filter_closed = filter_closed
        self.allow_dinner_only = allow_dinner_only

        # Initialize Jev Decision Client
        self.jev_client = jev_client
        if self.jev_client is None and JevDecisionClient is not None:
            candidate_key = (
                typesafe_api_key
                or kwargs.get("typesafe_key")
                or kwargs.get("jev_key")
                or openrouter_api_key
                or os.environ.get("TYPESAFE_API_KEY")
                or os.environ.get("JEV_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY", "")
            )
            if candidate_key:
                self.jev_client = JevDecisionClient(api_key=candidate_key)
            else:
                default_client = JevDecisionClient()
                if default_client.is_ready():
                    self.jev_client = default_client

        self._build_exclusion_indexes()
        if not self.contracted_data and not self.visited_data:
            print("  [Info] 未配置或未指定排除数据源，跳过排除过滤 (所有候选商家平滑准入)")
        if self.exclude_regions:
            print(f"  [Config] 已配置地理禁行/避开区域: {', '.join(self.exclude_regions)}")
        if self.include_regions:
            print(f"  [Config] 已配置地理限定区域: {', '.join(self.include_regions)}")
        if self.exclude_restaurants:
            print(f"  [Config] 已配置临时排除餐馆: {', '.join(self.exclude_restaurants)}")
        if self.mandatory_restaurants:
            print(f"  [Config] 已配置指定必选餐馆: {', '.join(self.mandatory_restaurants)}")
        if self.filter_closed:
            dinner_desc = "允许夜宵酒吧" if self.allow_dinner_only else "白天非营业时段自动过滤"
            print(f"  [Status] 已启用开业状态与公休日校验 (拜访日: {self.visit_date or '今日'}, 预定出发: {self.departure_time}, {dinner_desc})")
        if self.jev_client and getattr(self.jev_client, "is_ready", lambda: False)():
            print("  [AI Model] TypeSafe Jev 决策模型已就绪 (官方 SystemOne 接口)")

    def is_mandatory(self, restaurant: Dict) -> Tuple[bool, str]:
        """Checks if a restaurant is explicitly requested or pinned by the user."""
        if restaurant.get("_is_pinned"):
            return True, "[用户指定必选] 已置顶为今日必拜访商家"
        if not self.mandatory_restaurants:
            return False, ""
        name = (restaurant.get("name") or "").lower()
        addr = (restaurant.get("address") or "").lower()
        pid = (restaurant.get("placeId") or restaurant.get("id") or "").lower()
        for mand in self.mandatory_restaurants:
            if mand and (mand in name or mand in addr or mand == pid):
                return True, f"[用户指定必选] 匹配必选关键词: '{mand}'"
        return False, ""

    def is_manually_excluded(self, restaurant: Dict) -> Tuple[bool, str]:
        """Checks if a restaurant is explicitly excluded by the user."""
        if not self.exclude_restaurants:
            return False, ""
        name = (restaurant.get("name") or "").lower()
        addr = (restaurant.get("address") or "").lower()
        pid = (restaurant.get("placeId") or restaurant.get("id") or "").lower()
        for ex in self.exclude_restaurants:
            if ex and (ex in name or ex in addr or ex == pid):
                return True, f"[手动指定排除] 命中排除餐馆: '{ex}'"
        return False, ""

    def is_region_excluded(self, restaurant: Dict) -> Tuple[bool, str]:
        """
        Evaluates geofencing constraints:
        - Negative constraint: If address/region contains any word in exclude_regions -> Exclude!
        - Positive constraint: If include_regions is specified, address/region MUST contain at least one -> Exclude if not matched!
        """
        addr = (restaurant.get("address") or "").lower()
        region = (restaurant.get("region") or "").lower()
        name = (restaurant.get("name") or "").lower()
        full_loc = f"{addr} {region} {name}"

        # 1. Negative geofence (e.g. "不要去士嘉堡" -> exclude_regions=['scarborough'])
        for ex in self.exclude_regions:
            if ex in full_loc:
                return True, f"[地理禁行] 命中避开关键词: '{ex}'"

        # 2. Positive geofence (e.g. "在万锦范围内找" -> include_regions=['markham'])
        if self.include_regions:
            matched = any(inc in full_loc for inc in self.include_regions)
            if not matched:
                return True, f"[区域限定] 不在指定范围内 ({', '.join(self.include_regions)})"

        return False, ""

    def _build_exclusion_indexes(self):
        self.contracted_place_ids: Set[str] = set()
        self.contracted_phones: Set[str] = set()
        self.contracted_names: Set[str] = set()

        for item in self.contracted_data:
            pid = item.get("placeId") or item.get("place_id") or item.get("id")
            if pid:
                self.contracted_place_ids.add(pid)
            phone = normalize_phone(item.get("phone") or item.get("nationalPhoneNumber", ""))
            if phone and len(phone) >= 7:
                self.contracted_phones.add(phone)
            name = normalize_text(item.get("name") or "")
            if name:
                self.contracted_names.add(name)

        self.visited_place_ids: Set[str] = set()
        self.visited_phones: Set[str] = set()
        self.visited_names: Set[str] = set()

        for item in self.visited_data:
            pid = item.get("placeId") or item.get("place_id") or item.get("id")
            if pid:
                self.visited_place_ids.add(pid)
            phone = normalize_phone(item.get("phone") or item.get("nationalPhoneNumber", ""))
            if phone and len(phone) >= 7:
                self.visited_phones.add(phone)
            name = normalize_text(item.get("name") or "")
            if name:
                self.visited_names.add(name)

    def is_contracted(self, restaurant: Dict) -> Tuple[bool, str]:
        """
        Returns: (is_contracted, match_reason)
        """
        # Tier 1: Deterministic Hard Match
        pid = restaurant.get("placeId") or restaurant.get("id", "")
        if pid and pid in self.contracted_place_ids:
            return True, f"[精确匹配] Place ID: {pid}"

        phone = normalize_phone(restaurant.get("phone") or restaurant.get("nationalPhoneNumber", ""))
        if phone and phone in self.contracted_phones:
            return True, f"[精确匹配] 电话号码: {phone}"

        norm_name = normalize_text(restaurant.get("name") or "")
        if norm_name and norm_name in self.contracted_names:
            return True, f"[精确匹配] 名称完全一致: {norm_name}"

        # Tier 2: Model Semantic Entity Resolution (Jev or Heuristic)
        matched, conf, rationale, ref = semantic_entity_match(restaurant, self.contracted_data, jev_client=self.jev_client)
        if matched and conf >= 0.70:
            ref_name = ref.get("name", "") if ref else ""
            return True, f"[模型语义对齐] 与签约商家 '{ref_name}' 匹配 ({rationale}, 置信度: {int(conf*100)}%)"

        return False, ""

    def is_visited(self, restaurant: Dict) -> Tuple[bool, str]:
        """
        Returns: (is_visited, match_reason)
        """
        # Tier 1: Deterministic Hard Match
        pid = restaurant.get("placeId") or restaurant.get("id", "")
        if pid and pid in self.visited_place_ids:
            return True, f"[精确匹配] Place ID: {pid}"

        phone = normalize_phone(restaurant.get("phone") or restaurant.get("nationalPhoneNumber", ""))
        if phone and phone in self.visited_phones:
            return True, f"[精确匹配] 电话号码: {phone}"

        norm_name = normalize_text(restaurant.get("name") or "")
        if norm_name and norm_name in self.visited_names:
            return True, f"[精确匹配] 名称完全一致: {norm_name}"

        # Tier 2: Model Semantic Entity Resolution (Jev or Heuristic)
        matched, conf, rationale, ref = semantic_entity_match(restaurant, self.visited_data, jev_client=self.jev_client)
        if matched and conf >= 0.70:
            ref_name = ref.get("name", "") if ref else ""
            return True, f"[模型语义对齐] 与拜访记录 '{ref_name}' 匹配 ({rationale}, 置信度: {int(conf*100)}%)"

        return False, ""

    def filter_restaurants(self, candidates: List[Dict]) -> Tuple[List[Dict], Dict]:
        accepted = []
        stats = {
            "total_input": len(candidates),
            "mandatory_count": 0,
            "excluded_manual": 0,
            "excluded_regions": 0,
            "excluded_closed": 0,
            "excluded_contracted": 0,
            "excluded_visited": 0,
            "hard_match_count": 0,
            "semantic_match_count": 0,
            "mandatory_details": [],
            "manual_details": [],
            "region_details": [],
            "closed_details": [],
            "contracted_details": [],
            "visited_details": [],
            "accepted_count": 0
        }

        seen_pids = set()

        for r in candidates:
            pid = r.get("placeId") or r.get("id", "")
            if pid and pid in seen_pids:
                continue
            if pid:
                seen_pids.add(pid)

            # 1. Mandatory user inclusion (highest priority, overrides exclusion)
            is_mand, mand_reason = self.is_mandatory(r)
            if is_mand:
                r_copy = dict(r)
                r_copy["_is_pinned"] = True
                r_copy["_pinned_reason"] = mand_reason
                stats["mandatory_count"] += 1
                stats["mandatory_details"].append({
                    "name": r.get("name"),
                    "reason": mand_reason
                })
                accepted.append(r_copy)
                continue

            # 2. Manual restaurant exclusion (e.g. "不要去 KFC")
            is_man_ex, man_reason = self.is_manually_excluded(r)
            if is_man_ex:
                stats["excluded_manual"] += 1
                stats["manual_details"].append({
                    "name": r.get("name"),
                    "reason": man_reason
                })
                continue

            # 3. Spatial geofencing constraints
            is_reg, reg_reason = self.is_region_excluded(r)
            if is_reg:
                stats["excluded_regions"] += 1
                stats["region_details"].append({
                    "name": r.get("name"),
                    "reason": reg_reason
                })
                continue

            # 4. Opening Hours & Operational Status Check (Closed/Rest day/Late night only)
            if self.filter_closed:
                is_open, closed_reason = check_restaurant_open_status(
                    restaurant=r,
                    visit_date_str=self.visit_date,
                    departure_time_str=self.departure_time,
                    allow_dinner_only=self.allow_dinner_only
                )
                if not is_open:
                    stats["excluded_closed"] += 1
                    stats["closed_details"].append({
                        "name": r.get("name"),
                        "reason": closed_reason
                    })
                    continue

            is_c, c_reason = self.is_contracted(r)
            if is_c:
                stats["excluded_contracted"] += 1
                c_tier = "semantic_model" if "[模型语义对齐]" in c_reason else "hard"
                if c_tier == "hard":
                    stats["hard_match_count"] += 1
                else:
                    stats["semantic_match_count"] += 1

                stats["contracted_details"].append({
                    "name": r.get("name"),
                    "reason": c_reason,
                    "tier": c_tier
                })
                continue

            is_v, v_reason = self.is_visited(r)
            if is_v:
                stats["excluded_visited"] += 1
                v_tier = "semantic_model" if "[模型语义对齐]" in v_reason else "hard"
                if v_tier == "hard":
                    stats["hard_match_count"] += 1
                else:
                    stats["semantic_match_count"] += 1

                stats["visited_details"].append({
                    "name": r.get("name"),
                    "reason": v_reason,
                    "tier": v_tier
                })
                continue

            accepted.append(r)

        stats["accepted_count"] = len(accepted)
        return accepted, stats

