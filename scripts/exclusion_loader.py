#!/usr/bin/env python3
"""
Multi-Source Exclusion Loader and Schema Normalizer for Daily Restaurant Lead Scout.
Handles ingestion of arbitrary/heterogeneous merchant data from:
1. REST API endpoints (HTTP/HTTPS GET returning JSON).
2. Local Excel spreadsheets (.xlsx, .xls).
3. CSV files with arbitrary encodings (UTF-8, UTF-8-SIG, GBK).
4. Standard JSON files.

Includes fuzzy schema normalizer (Schema-on-read) that maps diverse column names
(e.g., '商户名', 'ShopName', 'Store Title', '客户') to canonical entity attributes.
"""

import os
import re
import csv
import json
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

# Fuzzy header mapping dictionaries
FIELD_SYNONYMS = {
    "name": [
        "name", "restaurant", "store", "shop", "title", "merchant", "client", "business",
        "餐馆", "商家", "店名", "商户", "商户名", "门店名称", "客户名称", "客户", "名称", "招牌", "餐厅名称"
    ],
    "phone": [
        "phone", "tel", "mobile", "telephone", "contact", "phone_number", "contact_number",
        "电话", "手机", "手机号", "联系电话", "联系方式", "联系人电话", "座机"
    ],
    "address": [
        "address", "addr", "location", "formatted_address", "street", "full_address",
        "地址", "详细地址", "门店地址", "商户地址", "位置", "街道", "经营地址"
    ],
    "place_id": [
        "place_id", "placeid", "id", "google_id", "google_place_id", "pid",
        "谷歌id", "地点id", "商户id"
    ]
}

def clean_key(key: Any) -> str:
    """Normalizes dictionary keys or column headers for fuzzy matching."""
    if not key:
        return ""
    s = str(key).lower().strip()
    s = re.sub(r"[_\-\s\(\)\[\]（）/]+", "", s)
    return s

def match_field_role(header_text: str) -> Optional[str]:
    """Determines canonical field role (name, phone, address, place_id) from raw header."""
    normalized_header = clean_key(header_text)
    if not normalized_header:
        return None

    for canonical_field, synonyms in FIELD_SYNONYMS.items():
        for syn in synonyms:
            norm_syn = clean_key(syn)
            if normalized_header == norm_syn or norm_syn in normalized_header:
                return canonical_field
    return None

def normalize_raw_record(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Intelligently maps arbitrary key-value dict into canonical entity:
    {
      "name": str,
      "phone": str,
      "address": str,
      "placeId": str,
      "_raw": dict
    }
    """
    canonical: Dict[str, Any] = {
        "name": "",
        "phone": "",
        "address": "",
        "placeId": "",
        "_raw": raw_item
    }

    # Step 1: Direct key match
    for k, v in raw_item.items():
        if v is None or v == "":
            continue
        val_str = str(v).strip()
        role = match_field_role(k)
        if role:
            if role == "place_id" and not canonical["placeId"]:
                canonical["placeId"] = val_str
            elif role == "name" and not canonical["name"]:
                canonical["name"] = val_str
            elif role == "phone" and not canonical["phone"]:
                canonical["phone"] = val_str
            elif role == "address" and not canonical["address"]:
                canonical["address"] = val_str

    # Fallback heuristic: If name is still empty, look for first non-numeric text column
    if not canonical["name"]:
        for k, v in raw_item.items():
            if v and isinstance(v, str) and len(v) < 80 and not re.match(r"^\+?\d+$", v.strip()):
                canonical["name"] = v.strip()
                break

    return canonical

def load_from_http_api(url: str, timeout: int = 10) -> List[Dict[str, Any]]:
    """Loads exclusion records from a REST API endpoint."""
    req = urllib.request.Request(url, headers={"User-Agent": "LeadScout-EntityMatcher/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8")
        data = json.load(content)
        # Support nested structures like {"data": [...]}, {"items": [...]}, {"records": [...]}
        if isinstance(data, list):
            return [normalize_raw_record(x) for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for candidate_key in ["data", "items", "records", "merchants", "restaurants", "results"]:
                if candidate_key in data and isinstance(data[candidate_key], list):
                    return [normalize_raw_record(x) for x in data[candidate_key] if isinstance(x, dict)]
            # If dictionary with single object
            return [normalize_raw_record(data)]
    return []

def load_from_csv(filepath: str) -> List[Dict[str, Any]]:
    """Loads records from CSV with multiple encoding fallbacks."""
    encodings = ["utf-8-sig", "utf-8", "gbk", "latin-1"]
    for enc in encodings:
        try:
            with open(filepath, "r", encoding=enc) as f:
                reader = csv.DictReader(f)
                results = []
                for row in reader:
                    results.append(normalize_raw_record(row))
                return results
        except (UnicodeDecodeError, csv.Error):
            continue
    print(f"[Warning] Failed to parse CSV with standard encodings: {filepath}")
    return []

def load_from_excel(filepath: str) -> List[Dict[str, Any]]:
    """Loads records from Excel (.xlsx, .xls) using openpyxl."""
    try:
        import openpyxl
    except ImportError:
        print(f"[Warning] openpyxl not installed. Please install openpyxl to parse Excel exclusions: {filepath}")
        return []

    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # Find header row (first non-empty row)
        headers = []
        start_row_idx = 0
        for idx, r in enumerate(rows):
            if any(cell is not None for cell in r):
                headers = [str(cell) if cell is not None else f"col_{c_idx}" for c_idx, cell in enumerate(r)]
                start_row_idx = idx + 1
                break

        results = []
        for r in rows[start_row_idx:]:
            if not any(cell is not None for cell in r):
                continue
            row_dict = {}
            for col_idx, h in enumerate(headers):
                val = r[col_idx] if col_idx < len(r) else None
                if val is not None:
                    row_dict[h] = val
            results.append(normalize_raw_record(row_dict))
        return results
    except Exception as e:
        print(f"[Warning] Could not read Excel file {filepath}: {e}")
        return []

def load_from_json_file(filepath: str) -> List[Dict[str, Any]]:
    """Loads records from local JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [normalize_raw_record(x) for x in data if isinstance(x, dict)]
            if isinstance(data, dict):
                for candidate_key in ["data", "items", "records", "merchants", "restaurants", "results"]:
                    if candidate_key in data and isinstance(data[candidate_key], list):
                        return [normalize_raw_record(x) for x in data[candidate_key] if isinstance(x, dict)]
                return [normalize_raw_record(data)]
    except Exception as e:
        print(f"[Warning] Could not parse JSON file {filepath}: {e}")
    return []

def load_exclusion_source(source: Optional[str]) -> List[Dict[str, Any]]:
    """
    Polymorphic loader: Ingests exclusion data from any supported protocol/format.
    - HTTP / HTTPS REST API
    - Local Excel (.xlsx, .xls)
    - Local CSV (.csv)
    - Local JSON (.json)
    """
    if not source:
        return []

    src = source.strip()

    # 1. HTTP / HTTPS API
    if src.startswith("http://") or src.startswith("https://"):
        try:
            print(f"  [Info] Ingesting exclusion records from API: {src}...")
            records = load_from_http_api(src)
            print(f"  [Success] Successfully normalized {len(records)} records from API")
            return records
        except Exception as e:
            print(f"  [Warning] Could not fetch from exclusion API ({src}): {e}")
            return []

    # Local file checks
    expanded_path = os.path.expanduser(src)
    if not os.path.exists(expanded_path):
        return []

    lower_path = expanded_path.lower()
    if lower_path.endswith(".xlsx") or lower_path.endswith(".xls"):
        return load_from_excel(expanded_path)
    elif lower_path.endswith(".csv"):
        return load_from_csv(expanded_path)
    elif lower_path.endswith(".json"):
        return load_from_json_file(expanded_path)
    else:
        # Try JSON first, then CSV
        res = load_from_json_file(expanded_path)
        if not res:
            res = load_from_csv(expanded_path)
        return res
