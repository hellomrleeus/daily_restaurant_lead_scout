#!/usr/bin/env python3
"""
Simplified Opening Hours Algorithm for Field Sales.
Normalizes complex raw Google Places opening hours into a concise, professional English string:
e.g.
  "11:00 AM – 10:00 PM"
  "11:00 AM – 10:00 PM (Fri: 11:00 AM – 11:00 PM)"
  "11:00 AM – 10:00 PM (Mon Closed)"
  "11:30 AM – 9:30 PM (Mon, Tue Closed, Fri: 11:30 AM – 10:30 PM)"
"""

import re
from typing import Union, List, Dict, Tuple, Optional

WEEKDAYS = [
    {"dayIndex": 1, "name_en": "Mon", "regex": re.compile(r"^(?:monday\b|mon\b|星期一|周一|礼拜一)(?![~–\-至到])[\s:：]*", re.IGNORECASE)},
    {"dayIndex": 2, "name_en": "Tue", "regex": re.compile(r"^(?:tuesday\b|tue\b|星期二|周二|礼拜二)(?![~–\-至到])[\s:：]*", re.IGNORECASE)},
    {"dayIndex": 3, "name_en": "Wed", "regex": re.compile(r"^(?:wednesday\b|wed\b|星期三|周三|礼拜三)(?![~–\-至到])[\s:：]*", re.IGNORECASE)},
    {"dayIndex": 4, "name_en": "Thu", "regex": re.compile(r"^(?:thursday\b|thu\b|星期四|周四|礼拜四)(?![~–\-至到])[\s:：]*", re.IGNORECASE)},
    {"dayIndex": 5, "name_en": "Fri", "regex": re.compile(r"^(?:friday\b|fri\b|星期五|周五|礼拜五)(?![~–\-至到])[\s:：]*", re.IGNORECASE)},
]

def normalize_time_to_english(text: Optional[str]) -> str:
    """
    Normalizes any opening hours fragment, time range, or summary string into pure English.
    Translates Chinese day names, AM/PM indicators, 'Closed' keywords, and 24h expressions.
    """
    if not text:
        return "Not provided"
    s = str(text).strip()
    if not s or s.lower() in ("未提供", "无", "not provided", "none", "null", "未知", "暂无", "未知营业时间", "暂无数据", "暂未提供"):
        return "Not provided"

    # Quick matches for Closed and 24 Hours
    if re.search(r"^(?:closed|close|off|休息|打烊|不营业|公休|闭店|停业|关门|整日休息)$", s, re.I):
        return "Closed"
    if re.search(r"^(?:(?:周一至周日|星期一至星期日|每天|每日|daily)\s*[:：]?)?\s*(?:24\s*小时营业|全天营业|24\s*小时|24\s*hours?|open\s*24\s*hours?)$", s, re.I):
        return "Open 24 hours"

    # Day token replacements
    day_replacements = [
        (r"星期一|周一|礼拜一", "Mon"),
        (r"星期二|周二|礼拜二", "Tue"),
        (r"星期三|周三|礼拜三", "Wed"),
        (r"星期四|周四|礼拜四", "Thu"),
        (r"星期五|周五|礼拜五", "Fri"),
        (r"星期六|周六|礼拜六", "Sat"),
        (r"星期日|星期天|周日|周天|礼拜日|礼拜天", "Sun"),
        (r"平时|工作日|周一至周五", "Mon–Fri"),
        (r"周末", "Weekends"),
        (r"每天|每日", "Daily"),
        (r"24\s*小时营业|全天营业|24\s*小时", "Open 24 hours"),
        (r"\s*(?:休息|打烊|不营业|公休|闭店|关门)\s*", " Closed "),
        (r"未提供|暂无|未知", "Not provided"),
    ]
    for pat, repl in day_replacements:
        s = re.sub(pat, repl, s)

    # Chinese punctuation & symbols
    punct_replacements = [
        (r"、", ", "),
        (r"，", ", "),
        (r"（", " ("),
        (r"）", ")"),
        (r"：", ": "),
        (r"\s*[-–~至到]\s*", " – "),
    ]
    for pat, repl in punct_replacements:
        s = re.sub(pat, repl, s)

    # Convert Chinese time prefixes/suffixes to AM / PM
    s = re.sub(r"(?:上午|早上|早晨)\s*(\d{1,2}(?::\d{2})?)", r"\1 AM", s)
    s = re.sub(r"(\d{1,2}(?::\d{2})?)\s*(?:上午|早上|早晨)", r"\1 AM", s)

    s = re.sub(r"(?:下午|晚上|傍晚|晚间)\s*(\d{1,2}(?::\d{2})?)", r"\1 PM", s)
    s = re.sub(r"(\d{1,2}(?::\d{2})?)\s*(?:下午|晚上|傍晚|晚间)", r"\1 PM", s)

    s = re.sub(r"(?:中午)\s*(\d{1,2}(?::\d{2})?)", r"\1 PM", s)
    s = re.sub(r"(\d{1,2}(?::\d{2})?)\s*(?:中午)", r"\1 PM", s)

    s = re.sub(r"(?:凌晨|半夜|深夜)\s*(\d{1,2}(?::\d{2})?)", r"\1 AM", s)
    s = re.sub(r"(\d{1,2}(?::\d{2})?)\s*(?:凌晨|半夜|深夜)", r"\1 AM", s)

    # Handle Chinese hour expressions without colons
    s = re.sub(r"(\d{1,2})\s*点半", r"\1:30", s)
    s = re.sub(r"(\d{1,2})\s*(?:点|时)", r"\1:00", s)

    # Convert 24-hour ranges (e.g. 11:00 – 22:00) to 12-hour AM/PM if no AM/PM indicator exists
    def convert_range_match(m):
        h1_str, m1_str, h2_str, m2_str = m.group(1), m.group(2), m.group(3), m.group(4)
        h1 = int(h1_str)
        m1 = m1_str or "00"
        h2 = int(h2_str)
        m2 = m2_str or "00"

        if h1 == 0:
            t1 = f"12:{m1} AM"
        elif h1 == 12:
            t1 = f"12:{m1} PM"
        elif h1 > 12:
            t1 = f"{h1 - 12}:{m1} PM"
        else:
            t1 = f"{h1}:{m1} AM"

        if h2 == 0 or h2 == 24:
            t2 = f"12:{m2} AM"
        elif h2 == 12:
            t2 = f"12:{m2} PM"
        elif h2 > 12:
            t2 = f"{h2 - 12}:{m2} PM"
        elif h2 < 6:
            t2 = f"{h2}:{m2} AM"
        else:
            t2 = f"{h2}:{m2} PM"
        return f"{t1} – {t2}"

    range_pattern = re.compile(r"\b(\d{1,2}):(\d{2})\s*–\s*(\d{1,2}):(\d{2})\b", re.I)
    if not re.search(r"\b(?:AM|PM)\b", s, re.I):
        s = range_pattern.sub(convert_range_match, s)

    # Clean whitespace and punctuation
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+([,\)])", r"\1", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r":\s*:", ":", s)
    return s.strip()

def format_weekday_opening_hours(raw_hours: Union[str, List[str], Dict, None], lang: str = "en") -> str:
    """
    Simplified weekday opening hours formatting algorithm.
    Focuses on Mon-Fri commercial hours and summarizes exceptions cleanly.
    Guarantees pure English output.
    """
    if not raw_hours:
        return "Not provided"

    # 1. Normalize input to string
    raw_str = ""
    if isinstance(raw_hours, str):
        raw_str = raw_hours
    elif isinstance(raw_hours, list):
        raw_str = "\n".join([str(x) for x in raw_hours])
    elif isinstance(raw_hours, dict):
        weekday_desc = raw_hours.get("weekdayDescriptions")
        if isinstance(weekday_desc, list):
            raw_str = "\n".join([str(x) for x in weekday_desc])
        else:
            raw_str = "\n".join([f"{k}: {v}" for k, v in raw_hours.items()])
    else:
        raw_str = str(raw_hours)

    # 2. Normalize spaces and zero-width characters
    clean_str = re.sub(r"[\u202F\u00A0\u2009\u200A\u3000]", " ", raw_str)
    clean_str = re.sub(r"[\u200B-\u200D\uFEFF]", "", clean_str).strip()

    if not clean_str or clean_str in ("未提供", "无", "Not provided", "None", "null", "未知", "暂无"):
        return "Not provided"

    # Check 24 hours fast path
    if re.search(r"^(?:(?:周一至周日|星期一至星期日|每天|每日|daily)\s*[:：]?)?\s*(?:24\s*小时营业|全天营业|24\s*小时|24\s*hours?|open\s*24\s*hours?)$", clean_str, re.I):
        return "Open 24 hours"

    # 3. Pre-process: insert newline before day names if glued together without newlines
    day_glue_pattern = re.compile(
        r"([^\r\n])\s*(?=(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon\b|tue\b|wed\b|thu\b|fri\b|sat\b|sun\b|星期[一二三四五六日天]|周[一二三四五六日天]|礼拜[一二三四五六日天])[\s:：])",
        re.IGNORECASE
    )
    clean_str = day_glue_pattern.sub(r"\1\n", clean_str)

    lines = [s.strip() for s in re.split(r"[\r\n;]+", clean_str) if s.strip()]

    parsed_days = {}
    for line in lines:
        for wd in WEEKDAYS:
            m = wd["regex"].match(line)
            if m:
                time_part = wd["regex"].sub("", line).strip()
                time_part = re.sub(r"\s+", " ", time_part)
                # Check closed
                if re.match(r"^(?:closed|close|休息|打烊|不营业|off)$", time_part, re.IGNORECASE) or "closed" in time_part.lower() or "休息" in time_part:
                    time_part = "Closed"
                else:
                    time_part = normalize_time_to_english(time_part)
                parsed_days[wd["dayIndex"]] = {
                    "name": wd["name_en"],
                    "time": time_part
                }
                break

    # If weekday entries are not found or fewer than 3 found (e.g. single line range or malformed)
    if len(parsed_days) < 3:
        prefix_pattern = re.compile(
            r"^(?:(?:mon(?:day)?|周一|星期一|礼拜一)\s*[-–~至到]\s*(?:sun(?:day)?|fri(?:day)?|周日|周天|星期日|星期天|周五|星期五|礼拜日|礼拜天|礼拜五)|每天|每日|daily)[\s:：]*",
            re.IGNORECASE
        )
        simplified = prefix_pattern.sub("", clean_str).strip()
        return normalize_time_to_english(simplified or clean_str)

    # Mon-Fri (1..5)
    weekday_list = [parsed_days.get(idx) for idx in range(1, 6)]

    # Count frequencies of known times
    freq_map = {}
    for item in weekday_list:
        if item and item["time"]:
            t = item["time"]
            freq_map[t] = freq_map.get(t, 0) + 1

    # Find most frequent time (usual time). Prefer non-Closed if counts tie
    usual_time = ""
    max_score = -1
    for t, count in freq_map.items():
        score = count * 10 + (1 if t != "Closed" else 0)
        if score > max_score:
            max_score = score
            usual_time = t

    # Fill any missing weekday with usual_time
    for i in range(5):
        if not weekday_list[i]:
            weekday_list[i] = {"name": WEEKDAYS[i]["name_en"], "time": usual_time}

    # Check if all 5 weekdays equal usual_time
    special_days = [item for item in weekday_list if item["time"] != usual_time]
    if not special_days:
        return usual_time

    # Group special days by their time
    special_days_by_time = {}
    for item in special_days:
        t = item["time"]
        special_days_by_time.setdefault(t, []).append(item["name"])

    special_parts = []
    for t, day_names in special_days_by_time.items():
        days_str = ", ".join(day_names)
        if t == "Closed":
            special_parts.append(f"{days_str} Closed")
        else:
            special_parts.append(f"{days_str}: {t}")

    joined_parts = ", ".join(special_parts)
    return f"{usual_time} ({joined_parts})"


ALL_DAYS = [
    {"index": 0, "short": "Mon", "full": "Monday", "aliases": ["monday", "mon", "星期一", "周一"]},
    {"index": 1, "short": "Tue", "full": "Tuesday", "aliases": ["tuesday", "tue", "星期二", "周二"]},
    {"index": 2, "short": "Wed", "full": "Wednesday", "aliases": ["wednesday", "wed", "星期三", "周三"]},
    {"index": 3, "short": "Thu", "full": "Thursday", "aliases": ["thursday", "thu", "星期四", "周四"]},
    {"index": 4, "short": "Fri", "full": "Friday", "aliases": ["friday", "fri", "星期五", "周五"]},
    {"index": 5, "short": "Sat", "full": "Saturday", "aliases": ["saturday", "sat", "星期六", "周六"]},
    {"index": 6, "short": "Sun", "full": "Sunday", "aliases": ["sunday", "sun", "星期日", "星期天", "周日", "周天"]},
]

def check_restaurant_open_status(
    restaurant: Dict,
    visit_date_str: str = "",
    departure_time_str: str = "09:30",
    allow_dinner_only: bool = False,
    max_acceptable_open_hour: int = 17
) -> Tuple[bool, str]:
    """
    Evaluates whether a restaurant is operational and open during the planned visit date and time window.
    Checks:
    1. Business Status (filters out CLOSED_PERMANENTLY and CLOSED_TEMPORARILY).
    2. Target Day-of-Week (filters out rest days, e.g. Monday/Tuesday Closed).
    3. Daytime Opening Window (filters out late-night/dinner-only bars opening >= 17:00 unless allow_dinner_only=True).

    Returns:
      (is_open: bool, reason: str)
    """
    from datetime import datetime

    # 1. Check Google Places Business Status
    b_status = (
        restaurant.get("business_status")
        or restaurant.get("businessStatus")
        or ""
    ).upper().strip()
    if b_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        return False, f"店铺未营运 (状态: {b_status})"

    # If no visit date provided, default to today
    if not visit_date_str:
        visit_date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        dt = datetime.strptime(visit_date_str.strip(), "%Y-%m-%d")
        weekday_idx = dt.weekday()  # 0: Mon, 6: Sun
    except Exception:
        # Invalid date format: pass through
        return True, "日期格式未指定，默认准入"

    target_day = ALL_DAYS[weekday_idx]
    day_short = target_day["short"]
    day_full = target_day["full"]
    google_day = (weekday_idx + 1) % 7  # Google API: 0 is Sun, 1 is Mon...

    reg_hours = restaurant.get("regularOpeningHours") or restaurant.get("regular_opening_hours") or {}
    periods = reg_hours.get("periods") if isinstance(reg_hours, dict) else []

    # 2. Check structured periods if available
    if isinstance(periods, list) and periods:
        # Open 24/7 check (single period starting Sun 00:00 with no close)
        if len(periods) == 1 and "close" not in periods[0]:
            return True, "全天24小时营业"

        day_periods = [p for p in periods if (p.get("open") or {}).get("day") == google_day]
        if not day_periods:
            return False, f"计划拜访日公休 ({day_full} Closed)"

        if not allow_dinner_only:
            earliest_hour = min((p.get("open") or {}).get("hour", 0) for p in day_periods)
            earliest_min = min((p.get("open") or {}).get("minute", 0) for p in day_periods if (p.get("open") or {}).get("hour") == earliest_hour)
            if earliest_hour >= max_acceptable_open_hour:
                return False, f"仅晚间夜宵营业 (开门时间: {earliest_hour:02d}:{earliest_min:02d}，晚于 {max_acceptable_open_hour}:00)"

        return True, "正常营业"

    # 3. Check weekdayDescriptions list or raw text
    weekday_desc = (
        reg_hours.get("weekdayDescriptions")
        if isinstance(reg_hours, dict)
        else restaurant.get("rawOpeningHours")
    )
    if isinstance(weekday_desc, list) and weekday_desc:
        for line in weekday_desc:
            line_str = str(line).strip()
            line_lower = line_str.lower()
            if any(alias in line_lower for alias in target_day["aliases"]):
                # Found the target day description
                if any(w in line_lower for w in ["closed", "close", "休息", "打烊", "不营业"]):
                    return False, f"计划拜访日公休 ({day_full} Closed)"
                
                if "24 hours" in line_lower or "24小时" in line_lower:
                    return True, "24小时营业"

                if not allow_dinner_only:
                    # Parse opening hour from line (e.g. "Monday: 5:30 PM – 3:00 AM" or "17:00 - 22:00")
                    time_m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–~至到]", line_str, re.IGNORECASE)
                    if time_m:
                        h = int(time_m.group(1))
                        meridiem = (time_m.group(3) or "").upper()
                        if meridiem == "PM" and h < 12:
                            h += 12
                        elif meridiem == "AM" and h == 12:
                            h = 0
                        if h >= max_acceptable_open_hour:
                            return False, f"仅晚间夜宵营业 (开门时间: {line_str.split(':')[-1].strip()})"
                return True, "正常营业"

    # 4. Check formatted openingHours summary string (e.g. "11:30 AM – 8:00 PM (Mon Closed)")
    summary_str = str(restaurant.get("openingHours") or "").strip()
    if summary_str and summary_str != "Not provided":
        sum_lower = summary_str.lower()
        # Direct closed keyword for target day
        closed_pat = re.compile(rf"\b{day_short}\b[^\)]*closed", re.IGNORECASE)
        if closed_pat.search(summary_str):
            return False, f"计划拜访日公休 ({day_short} Closed)"

        if not allow_dinner_only:
            # Check if general usual time is late evening (e.g. starts with 5:xx PM or 17:xx)
            time_m = re.search(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–~至到]", summary_str, re.IGNORECASE)
            if time_m:
                h = int(time_m.group(1))
                meridiem = (time_m.group(3) or "").upper()
                if meridiem == "PM" and h < 12:
                    h += 12
                elif meridiem == "AM" and h == 12:
                    h = 0
                if h >= max_acceptable_open_hour:
                    return False, f"仅晚间夜宵营业 (开门时间: {summary_str})"

    return True, "正常营业"

