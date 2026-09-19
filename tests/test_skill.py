#!/usr/bin/env python3
"""
Unit and regression tests for Daily Restaurant Lead Scout Skill.
Tests:
1. Filter logic (contracted & visited exclusion)
2. Directional corridor projection & anti-shuttle slice sweep
3. Full 30-stop slash-concatenated Google Maps URL generation
4. Simplified weekday opening hours algorithm (English format)
5. 5-column spreadsheet export structure
"""

import os
import sys
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from filters import RestaurantFilter, normalize_phone, semantic_entity_match
from opening_hours import format_weekday_opening_hours
from directional_router import DirectionalRouter, project_to_corridor, get_bearing_unit_vector, generate_radial_probe_points, two_opt_tour, haversine_distance_km
from route_generator import RouteGenerator, build_google_maps_slash_url
from sheet_exporter import SheetExporter, EXPORT_HEADERS, map_stop_to_5_columns, diagnose_error
from config_manager import extract_spreadsheet_id, get_temp_dir, get_user_cache_dir, load_effective_config, resolve_origin_input
from exclusion_loader import normalize_raw_record, load_exclusion_source
from filters import semantic_entity_match

class TestOpeningHours(unittest.TestCase):
    def test_all_same_weekday(self):
        hours = "\n".join([
            "Monday: 11:00 AM – 10:00 PM",
            "Tuesday: 11:00 AM – 10:00 PM",
            "Wednesday: 11:00 AM – 10:00 PM",
            "Thursday: 11:00 AM – 10:00 PM",
            "Friday: 11:00 AM – 10:00 PM",
            "Saturday: 11:00 AM – 11:00 PM",
            "Sunday: 11:00 AM – 10:00 PM"
        ])
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:00 AM – 10:00 PM")

    def test_friday_late(self):
        hours = "\n".join([
            "Monday: 11:00 AM – 10:00 PM",
            "Tuesday: 11:00 AM – 10:00 PM",
            "Wednesday: 11:00 AM – 10:00 PM",
            "Thursday: 11:00 AM – 10:00 PM",
            "Friday: 11:00 AM – 11:00 PM",
            "Saturday: 11:00 AM – 11:00 PM",
            "Sunday: 11:00 AM – 10:00 PM"
        ])
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:00 AM – 10:00 PM (Fri: 11:00 AM – 11:00 PM)")

    def test_monday_closed(self):
        hours = "\n".join([
            "Monday: Closed",
            "Tuesday: 11:00 AM – 10:00 PM",
            "Wednesday: 11:00 AM – 10:00 PM",
            "Thursday: 11:00 AM – 10:00 PM",
            "Friday: 11:00 AM – 10:00 PM"
        ])
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:00 AM – 10:00 PM (Mon Closed)")

    def test_glued_string(self):
        glued = "Monday: 9:00 AM – 8:00 PMTuesday: 9:00 AM – 8:00 PMWednesday: 9:00 AM – 8:00 PMThursday: 9:00 AM – 8:00 PMFriday: 9:00 AM – 8:00 PMSaturday: 9:00 AM – 8:00 PMSunday: 9:00 AM – 8:00 PM"
        result = format_weekday_opening_hours(glued)
        self.assertEqual(result, "9:00 AM – 8:00 PM")

    def test_chinese_weekdays_and_exceptions(self):
        hours = "\n".join([
            "周一: 休息",
            "周二: 11:00 AM – 10:00 PM",
            "周三: 11:00 AM – 10:00 PM",
            "周四: 11:00 AM – 10:00 PM",
            "周五: 11:00 AM – 10:00 PM"
        ])
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:00 AM – 10:00 PM (Mon Closed)")

    def test_chinese_time_of_day_am_pm(self):
        hours = "\n".join([
            "周一: 上午11:00至下午10:00",
            "周二: 上午11:00至下午10:00",
            "周三: 上午11:00至下午10:00",
            "周四: 上午11:00至下午10:00",
            "周五: 上午11:00至晚上11:00"
        ])
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:00 AM – 10:00 PM (Fri: 11:00 AM – 11:00 PM)")

    def test_chinese_single_line_range(self):
        self.assertEqual(format_weekday_opening_hours("周一至周日 11:00 - 22:00"), "11:00 AM – 10:00 PM")
        self.assertEqual(format_weekday_opening_hours("周一至周五 11:00-22:00"), "11:00 AM – 10:00 PM")

    def test_chinese_24_hours_and_unprovided(self):
        self.assertEqual(format_weekday_opening_hours("24小时营业"), "Open 24 hours")
        self.assertEqual(format_weekday_opening_hours("全天营业"), "Open 24 hours")
        self.assertEqual(format_weekday_opening_hours("周一至周日 24小时营业"), "Open 24 hours")
        self.assertEqual(format_weekday_opening_hours("未提供"), "Not provided")
        self.assertEqual(format_weekday_opening_hours("暂无"), "Not provided")

    def test_chinese_parenthesized_summary(self):
        hours = "11:30 AM – 9:30 PM (周一、周二休息, 周五: 11:30 AM – 10:30 PM)"
        result = format_weekday_opening_hours(hours)
        self.assertEqual(result, "11:30 AM – 9:30 PM (Mon, Tue Closed, Fri: 11:30 AM – 10:30 PM)")

class TestRestaurantFilter(unittest.TestCase):
    def setUp(self):
        import tempfile
        import json
        self.temp_dir = tempfile.TemporaryDirectory()
        self.contracted_file = os.path.join(self.temp_dir.name, "contracted.json")
        self.visited_file = os.path.join(self.temp_dir.name, "visited.json")
        with open(self.contracted_file, "w", encoding="utf-8") as f:
            json.dump([
                {"placeId": "ChIJ4csnNTPV1IkRanjeCREz1WY", "name": "Kate & Jan Hotdogs", "phone": "9055550101"}
            ], f)
        with open(self.visited_file, "w", encoding="utf-8") as f:
            json.dump([
                {"placeId": "ChIJc7Wn-MockVisited001", "name": "Wohebaobei Postpartum Meals", "phone": "4165559999"}
            ], f)
        self.filter = RestaurantFilter(self.contracted_file, self.visited_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_contracted_exclusion(self):
        mock_cand = {
            "placeId": "ChIJ4csnNTPV1IkRanjeCREz1WY",
            "name": "Kate & Jan Hotdogs",
            "phone": "9055550101"
        }
        is_c, _ = self.filter.is_contracted(mock_cand)
        self.assertTrue(is_c)

    def test_visited_exclusion(self):
        mock_cand = {
            "placeId": "ChIJc7Wn-MockVisited001",
            "name": "Wohebaobei Postpartum Meals"
        }
        is_v, _ = self.filter.is_visited(mock_cand)
        self.assertTrue(is_v)

    def test_empty_exclusion_sources_allow_all(self):
        empty_filter = RestaurantFilter(contracted_path=None, visited_path=None)
        mock_cand = {
            "placeId": "ChIJanyRandomPlaceId",
            "name": "Kate & Jan Hotdogs",
            "phone": "9055550101",
            "address": "123 Finch Ave E"
        }
        is_c, reason_c = empty_filter.is_contracted(mock_cand)
        is_v, reason_v = empty_filter.is_visited(mock_cand)
        self.assertFalse(is_c)
        self.assertEqual(reason_c, "")
        self.assertFalse(is_v)
        self.assertEqual(reason_v, "")

class TestDirectionalRouter(unittest.TestCase):
    def test_corridor_projection_east(self):
        u_x, u_y = get_bearing_unit_vector(90.0)
        s, w = project_to_corridor(0.0, 0.1, 0.0, 0.0, u_x, u_y)
        self.assertGreater(s, 5.0)
        self.assertAlmostEqual(w, 0.0, places=2)

    def test_anti_shuttle_slice_sweep(self):
        origin = {"latitude": 43.7615, "longitude": -79.4111, "name": "Base"}
        router = DirectionalRouter(origin=origin, bearing_degrees=90.0, corridor_width_km=5.0, max_depth_km=30.0, slice_length_km=2.0)

        candidates = [
            {"name": "Stop A1", "latitude": 43.7615, "longitude": -79.3800, "placeId": "A1"}, # ~2.5km
            {"name": "Stop A2", "latitude": 43.7620, "longitude": -79.3820, "placeId": "A2"}, # ~2.3km
            {"name": "Stop B1", "latitude": 43.7610, "longitude": -79.3300, "placeId": "B1"}, # ~6.5km
            {"name": "Stop B2", "latitude": 43.7625, "longitude": -79.3280, "placeId": "B2"}, # ~6.6km
        ]

        route = router.plan_unidirectional_route(candidates, target_count=4)
        self.assertEqual(len(route), 4)
        # Verify slice grouping: A1 and A2 are both visited before B1 and B2
        names = [r["name"] for r in route]
        self.assertTrue(names.index("Stop A1") < names.index("Stop B1"))
        self.assertTrue(names.index("Stop A2") < names.index("Stop B1"))

    def test_two_opt_untangling(self):
        # Crossed hourglass quad: p1->p2->p3->p4
        crossed = [
            {"name": "P1", "latitude": 43.7000, "longitude": -79.3000},
            {"name": "P2", "latitude": 43.7100, "longitude": -79.2000},
            {"name": "P3", "latitude": 43.7100, "longitude": -79.3000},
            {"name": "P4", "latitude": 43.7000, "longitude": -79.2000},
        ]
        def tour_len(t):
            d = 0.0
            c_lat, c_lng = 43.6532, -79.3832
            for x in t:
                d += haversine_distance_km(c_lat, c_lng, float(x["latitude"]), float(x["longitude"]))
                c_lat, c_lng = float(x["latitude"]), float(x["longitude"])
            return d

        orig_d = tour_len(crossed)
        uncrossed = two_opt_tour(crossed, 43.6532, -79.3832, preserve_slices=False)
        new_d = tour_len(uncrossed)
        self.assertLess(new_d, orig_d, f"2-Opt must reduce tour length (was {orig_d:.2f}, now {new_d:.2f})")

class TestSlashUrlAnd5Columns(unittest.TestCase):
    def test_slash_url_30_stops(self):
        stops = [{"name": f"Stop {i+1}", "address": f"{i+1} Main St, Toronto, ON"} for i in range(30)]
        url = build_google_maps_slash_url("Base HQ, Toronto, ON", stops)
        self.assertTrue(url.startswith("https://www.google.com/maps/dir/Base%20HQ%2C%20Toronto%2C%20ON/"))
        self.assertTrue(url.endswith("/"))
        # Count occurrences of Main%20St in the URL
        self.assertEqual(url.count("Main%20St"), 30)

    def test_7_column_schema(self):
        self.assertEqual(EXPORT_HEADERS, [
            "No.",
            "Restaurant Name",
            "Address",
            "Navigation Address",
            "Opening Hours",
            "Phone",
            "Fried Food Evidence"
        ])
        stop = {
            "name": "KFC",
            "address": "123 Yonge St, Toronto, ON",
            "openingHours": "Monday: 11:00 AM – 10:00 PM\nTuesday: 11:00 AM – 10:00 PM\nWednesday: 11:00 AM – 10:00 PM\nThursday: 11:00 AM – 10:00 PM\nFriday: 11:00 AM – 10:00 PM",
            "phone": "416-555-0199",
            "_audit_rationale": "检测到油炸菜品: fried chicken, french fries"
        }
        row = map_stop_to_5_columns(stop, 0)
        self.assertEqual(len(row), 7)
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "KFC")
        self.assertEqual(row[2], "123 Yonge St, Toronto, ON")
        self.assertEqual(row[3], "123 Yonge St, Toronto, ON, Canada")
        self.assertEqual(row[4], "11:00 AM – 10:00 PM")
        self.assertEqual(row[5], "416-555-0199")
        self.assertEqual(row[6], "Detected fried items: fried chicken, french fries")

    def test_7_column_schema_with_chinese_inputs(self):
        stop = {
            "name": "Korean BBQ & Wings",
            "address": "加拿大安大略省万锦市 100 Enterprise Blvd",
            "rawOpeningHours": "周一: 休息\n周二: 上午11:30至晚上10:00\n周三: 上午11:30至晚上10:00\n周四: 上午11:30至晚上10:00\n周五: 上午11:30至晚上11:00",
            "phone": "905-555-0123",
            "_audit_rationale": "特色韩式炸鸡",
            "_fried_dishes": ["炸鸡", "薯条"]
        }
        row = map_stop_to_5_columns(stop, 0)
        self.assertEqual(len(row), 7)
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "Korean BBQ & Wings")
        self.assertEqual(row[2], "Canada ON Markham 100 Enterprise Blvd")
        self.assertEqual(row[3], "Canada ON Markham 100 Enterprise Blvd")
        self.assertEqual(row[4], "11:30 AM – 10:00 PM (Mon Closed, Fri: 11:30 AM – 11:00 PM)")
        self.assertEqual(row[5], "905-555-0123")
        self.assertEqual(row[6], "Specialty Korean Fried Chicken")

    def test_export_markdown_summary(self):
        import tempfile
        from sheet_exporter import SheetExporter
        with tempfile.TemporaryDirectory() as td:
            exporter = SheetExporter(output_dir=td)
            stops = [
                {"name": "The Fry", "address": "4864 Yonge St", "openingHours": "12:00 PM – 2:00 AM", "phone": "416-546-6159"}
            ]
            meta = {
                "visit_date": "2026-09-20",
                "direction": "EAST",
                "origin_str": "Fairview Mall",
                "master_nav_url": "https://www.google.com/maps/dir/Fairview/TheFry/",
                "excel_path": "/tmp/route.xlsx",
                "csv_path": "/tmp/route.csv"
            }
            md_path = exporter.export_markdown_summary(stops, "test_summary.md", meta)
            self.assertTrue(os.path.exists(md_path))
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("The Fry", content)
            self.assertIn("https://www.google.com/maps/dir/Fairview/TheFry/", content)
            self.assertIn("| No. | Restaurant Name | Address | Navigation Address |", content)
            self.assertIn("Fried Food Evidence", content)

    def test_export_excel_url_cell_has_no_prefix(self):
        import tempfile
        import openpyxl
        from sheet_exporter import SheetExporter
        with tempfile.TemporaryDirectory() as td:
            exporter = SheetExporter(output_dir=td)
            stops = [
                {"name": "The Fry", "address": "4864 Yonge St", "openingHours": "12:00 PM – 2:00 AM", "phone": "416-546-6159", "_audit_rationale": "特色韩式炸鸡"}
            ]
            test_url = "https://www.google.com/maps/dir/Fairview/TheFry/"
            excel_path = exporter.export_excel(stops, "test_route.xlsx", master_nav_url=test_url)
            self.assertTrue(os.path.exists(excel_path))

            wb = openpyxl.load_workbook(excel_path)
            ws = wb.active
            cell_val = ws["A2"].value
            # Must be clean hyperlink text without prefix or emoji
            self.assertEqual(cell_val, "Google Maps Route Navigation")
            self.assertNotIn("Google Maps 30-Stop Navigation Link:", str(cell_val))
            self.assertNotIn("\U0001F697", str(cell_val))
            self.assertIsNotNone(ws["A2"].hyperlink)
            self.assertEqual(ws["A2"].hyperlink.target, test_url)
            # Check 4th column header is Navigation Address
            self.assertEqual(ws["D4"].value, "Navigation Address")
            # Check 7th column header and value
            self.assertEqual(ws["G4"].value, "Fried Food Evidence")
            self.assertEqual(ws["G5"].value, "Specialty Korean Fried Chicken")

    def test_canonicalize_navigation_address_strips_mall_units(self):
        from sheet_exporter import canonicalize_navigation_address
        self.assertEqual(
            canonicalize_navigation_address("5000 Hwy 7 Unit 2190, Markham, ON L3R 4M9, Canada"),
            "5000 Hwy 7, Markham, ON L3R 4M9, Canada"
        )
        self.assertEqual(
            canonicalize_navigation_address("CF Markville, 5000 Hwy 7, Markham, ON L3R 4M9"),
            "5000 Hwy 7, Markham, ON L3R 4M9, Canada"
        )
        self.assertEqual(
            canonicalize_navigation_address("Food Court, 1800 Sheppard Ave E, North York, ON M2J 5A7"),
            "1800 Sheppard Ave E, North York, ON M2J 5A7, Canada"
        )
        self.assertEqual(
            canonicalize_navigation_address("Hot Kitchen Section inside Walmart, 5000 Hwy 7, Markham, ON L3R 4M9"),
            "5000 Hwy 7, Markham, ON L3R 4M9, Canada"
        )

    def test_cluster_navigation_addresses_and_excel_cell_merging(self):
        import tempfile
        import openpyxl
        from sheet_exporter import SheetExporter, cluster_navigation_addresses

        stops = [
            {"name": "McDonalds", "address": "5000 Hwy 7 Unit 2190, Markham, ON L3R 4M9, Canada", "latitude": 43.8681, "longitude": -79.2882},
            {"name": "Subway", "address": "CF Markville, 5000 Hwy 7, Markham, ON L3R 4M9", "latitude": 43.8683, "longitude": -79.2885},
            {"name": "Bourbon St Grill", "address": "5000 Hwy 7, Markville Mall Food Court, Markham, ON L3R 4M9", "latitude": 43.8682, "longitude": -79.2880},
            {"name": "Pho Markham", "address": "4981 Hwy 7 Unit 13, Unionville, ON L3R 1N1", "latitude": 43.8665, "longitude": -79.2941},
            {"name": "Katsuya", "address": "First Markham Place, 3255 Hwy 7, Markham, ON L3R 3P9", "latitude": 43.8501, "longitude": -79.3551},
            {"name": "Chatime", "address": "3255 Highway 7 Unit 21, Markham, ON L3R 3P9", "latitude": 43.8502, "longitude": -79.3553},
        ]

        clustered = cluster_navigation_addresses(stops)
        # Verify 3 stores at Markville Mall share the same navigation address
        self.assertEqual(clustered[0]["navigation_address"], clustered[1]["navigation_address"])
        self.assertEqual(clustered[1]["navigation_address"], clustered[2]["navigation_address"])
        # Verify stores at First Markham Place share the same navigation address
        self.assertEqual(clustered[4]["navigation_address"], clustered[5]["navigation_address"])

        with tempfile.TemporaryDirectory() as td:
            exporter = SheetExporter(output_dir=td)
            excel_path = exporter.export_excel(clustered, "test_cluster_merge.xlsx")
            self.assertTrue(os.path.exists(excel_path))

            wb = openpyxl.load_workbook(excel_path)
            ws = wb.active
            merged_ranges = [str(mr) for mr in ws.merged_cells.ranges]
            # Rows 5, 6, 7 (McDonalds, Subway, Bourbon St Grill) must be merged in column D (col 4)
            self.assertIn("D5:D7", merged_ranges)
            # Rows 9, 10 (Katsuya, Chatime) must be merged in column D (col 4)
            self.assertIn("D9:D10", merged_ranges)

    def test_slash_url_collapses_same_mall_destinations(self):
        from sheet_exporter import cluster_navigation_addresses
        from route_generator import build_google_maps_slash_url

        stops = [
            {"name": "McDonalds", "address": "5000 Hwy 7 Unit 2190, Markham, ON L3R 4M9, Canada", "latitude": 43.8681, "longitude": -79.2882},
            {"name": "Subway", "address": "CF Markville, 5000 Hwy 7, Markham, ON L3R 4M9", "latitude": 43.8683, "longitude": -79.2885},
            {"name": "Bourbon St Grill", "address": "5000 Hwy 7, Markville Mall Food Court, Markham, ON L3R 4M9", "latitude": 43.8682, "longitude": -79.2880},
            {"name": "Pho Markham", "address": "4981 Hwy 7 Unit 13, Unionville, ON L3R 1N1", "latitude": 43.8665, "longitude": -79.2941},
        ]
        clustered = cluster_navigation_addresses(stops)
        url = build_google_maps_slash_url("Field Operations Base", clustered)
        # Markville Mall (5000 Hwy 7) should only appear ONCE in the navigation URL
        self.assertEqual(url.count("5000%20Hwy%207"), 1)

class TestAgentAuditManager(unittest.TestCase):
    def test_export_and_import_decisions(self):
        import tempfile
        from fried_model_auditor import AgentAuditManager
        test_audit_dir = os.path.join(tempfile.gettempdir(), "test_lead_scout_audit")
        os.makedirs(test_audit_dir, exist_ok=True)
        mgr = AgentAuditManager(test_audit_dir)

        sample = [{
            "placeId": "TEST_PID_01",
            "name": "Crispy Tenders",
            "primaryType": "restaurant",
            "address": "123 Test St",
            "photo_urls": ["http://example.com/photo.jpg"],
            "editorialSummary": "Serves fried chicken tenders and french fries."
        }]

        exported_path = mgr.export_pending_audit(sample)
        self.assertTrue(os.path.exists(exported_path))

        # Test agent decision override
        mgr.agent_decisions["TEST_PID_01"] = {
            "placeId": "TEST_PID_01",
            "is_fried": True,
            "dishes": ["chicken tenders", "french fries"],
            "notes": "Verified by agent multimodal inspection"
        }

        is_fried, conf, dishes, rationale = mgr.audit_restaurant(sample[0])
        self.assertTrue(is_fried)
        self.assertEqual(conf, 1.0)
        self.assertIn("chicken tenders", dishes)

class TestConfigAndTempDir(unittest.TestCase):
    def test_extract_spreadsheet_id_full_url(self):
        url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit#gid=0"
        self.assertEqual(extract_spreadsheet_id(url), "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")

    def test_extract_spreadsheet_id_share_link(self):
        url = "https://docs.google.com/spreadsheets/d/1w8aJkl92348_xyz/edit?usp=sharing"
        self.assertEqual(extract_spreadsheet_id(url), "1w8aJkl92348_xyz")

    def test_extract_spreadsheet_id_raw_id(self):
        raw = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
        self.assertEqual(extract_spreadsheet_id(raw), "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")

    def test_extract_spreadsheet_id_empty(self):
        self.assertEqual(extract_spreadsheet_id(""), "")
        self.assertEqual(extract_spreadsheet_id(None), "")

    def test_temp_and_cache_directories(self):
        tmp = get_temp_dir("audit")
        self.assertTrue(os.path.exists(tmp))
        self.assertIn("restaurant_lead_scout", tmp)

        cache = get_user_cache_dir("routes")
        self.assertTrue(os.path.exists(cache))

    def test_agent_auditor_default_temp_dir(self):
        from fried_model_auditor import AgentAuditManager
        mgr = AgentAuditManager()
        self.assertTrue(os.path.exists(mgr.audit_dir))
        self.assertIn("restaurant_lead_scout", mgr.audit_dir)

class TestExclusionLoaderAndEntityResolution(unittest.TestCase):
    def test_schema_normalizer_chinese_and_english(self):
        # Messy Chinese CRM record
        raw_cn = {
            "商户名称": "测试大盘鸡 (北约克分店)",
            "联系人电话": "416-555-8899",
            "经营地址": "123 Finch Ave E",
            "商户状态": "已签约"
        }
        norm_cn = normalize_raw_record(raw_cn)
        self.assertEqual(norm_cn["name"], "测试大盘鸡 (北约克分店)")
        self.assertEqual(norm_cn["phone"], "416-555-8899")
        self.assertEqual(norm_cn["address"], "123 Finch Ave E")

        # Messy English CRM record
        raw_en = {
            "Store Title": "Crispy Bird Wings Inc",
            "Tel": "+1-905-123-4567",
            "Formatted Location": "456 Yonge St, Toronto",
            "Google ID": "ChIJmockId99"
        }
        norm_en = normalize_raw_record(raw_en)
        self.assertEqual(norm_en["name"], "Crispy Bird Wings Inc")
        self.assertEqual(norm_en["phone"], "+1-905-123-4567")
        self.assertEqual(norm_en["address"], "456 Yonge St, Toronto")
        self.assertEqual(norm_en["placeId"], "ChIJmockId99")

    def test_semantic_entity_matching_same_branch(self):
        candidate = {
            "name": "bb.q Chicken Don Mills",
            "address": "38 Forest Manor Rd Unit B, North York, ON M2J 0H4 Canada"
        }
        # CRM has different spelling and formatting
        ref_records = [
            {
                "name": "BBQ Chicken (Forest Manor)",
                "address": "38 Forest Manor Road, North York"
            }
        ]
        matched, conf, rationale, _ = semantic_entity_match(candidate, ref_records)
        self.assertTrue(matched)
        self.assertGreaterEqual(conf, 0.90)
        self.assertIn("38", rationale)

    def test_semantic_entity_matching_different_branches_not_excluded(self):
        # Candidate is in Scarborough Ellesmere
        candidate = {
            "name": "Popeyes Louisiana Kitchen",
            "address": "85 Ellesmere Rd Unit H, Scarborough, ON M1R 4C1 Canada"
        }
        # CRM has a Popeyes branch in Markham Woodbine
        ref_records = [
            {
                "name": "Popeyes Louisiana Kitchen (Markham Hwy 7)",
                "address": "8500 Woodbine Ave, Markham, ON L3R 4X9"
            }
        ]
        matched, _, _, _ = semantic_entity_match(candidate, ref_records)
        # Should NOT match because they are different branches in different cities
        self.assertFalse(matched)

class TestFailureDiagnosisAndAlert(unittest.TestCase):
    def test_diagnose_api_key_error(self):
        err = Exception("Google Places API error 403: API key not valid or request denied.")
        cause, advice, retry_cmd = diagnose_error(err, {"direction": "north"})
        self.assertIn("API Key", cause)
        self.assertIn("Places API", advice)
        self.assertIn("--direction north", retry_cmd)

    def test_diagnose_quota_error(self):
        err = Exception("RESOURCE_EXHAUSTED: 429 Quota exceeded for query.")
        cause, advice, _ = diagnose_error(err)
        self.assertIn("配额", cause)
        self.assertIn("Google Cloud Console", advice)

    def test_diagnose_timeout_error(self):
        err = Exception("Connection timed out to maps.googleapis.com")
        cause, advice, _ = diagnose_error(err)
        self.assertIn("网络连接超时", cause)

    def test_diagnose_insufficient_candidates_error(self):
        err = ValueError("沿走廊探测到的商户少于目标数 (12 < 30)")
        cause, advice, retry_cmd = diagnose_error(err, {"direction": "east"})
        self.assertIn("不足", cause)
        self.assertIn("--corridor-width 6.0", retry_cmd)

    def test_report_failure_generates_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SheetExporter(output_dir=tmpdir)
            ctx = {
                "visit_date": "2026-09-17",
                "direction": "EAST",
                "sheet_url": "https://docs.google.com/spreadsheets/d/mock123/edit",
                "spreadsheet_id": "mock123"
            }
            err = RuntimeError("Simulated autonomous task crash")
            log_path = exporter.report_failure_to_google_sheet(
                spreadsheet_id="mock123",
                credentials_file=None,
                error=err,
                context=ctx
            )
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Simulated autonomous task crash", content)
                self.assertIn("Root Cause:", content)
                self.assertIn("Retry Command:", content)

class TestGoogleSheetWebhookSync(unittest.TestCase):
    def test_sync_to_google_sheet_with_apps_script_webhook(self):
        import json
        import tempfile
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SheetExporter(output_dir=tmpdir)
            stops = [
                {
                    "name": "Test Fryer",
                    "address": "123 Main St, Toronto, ON",
                    "rawOpeningHours": "11:00 AM – 10:00 PM",
                    "phone": "(416) 123-4567",
                    "_audit_rationale": "检测到油炸菜品: Wings"
                }
            ]
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"OK"
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None

            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                success = exporter.sync_to_google_sheet(
                    spreadsheet_id="https://script.google.com/macros/s/test-webhook-id/exec",
                    stops=stops,
                    master_nav_url="https://www.google.com/maps/dir/Origin/Dest"
                )
                self.assertTrue(success)
                self.assertTrue(mock_urlopen.called)
                req = mock_urlopen.call_args[0][0]
                self.assertEqual(req.full_url, "https://script.google.com/macros/s/test-webhook-id/exec")
                payload = json.loads(req.data.decode("utf-8"))
                self.assertEqual(len(payload["headers"]), 7)
                self.assertEqual(payload["headers"][3], "Navigation Address")
                self.assertEqual(payload["headers"][6], "Fried Food Evidence")
                self.assertEqual(payload["rows"][0][1], "Test Fryer")
                self.assertEqual(payload["rows"][0][6], "Detected fried items: Wings")

    def test_sync_to_google_sheet_carries_explicit_sheet_name(self):
        import json
        import tempfile
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SheetExporter(output_dir=tmpdir)
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"OK"
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None

            with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
                exporter.sync_to_google_sheet(
                    spreadsheet_id="https://script.google.com/macros/s/test-webhook-id/exec",
                    sheet_name="2026-09-18 (EAST)",
                    stops=[]
                )
                req = mock_urlopen.call_args[0][0]
                payload = json.loads(req.data.decode("utf-8"))
                self.assertEqual(payload["sheet_name"], "2026-09-18 (EAST)")

    def test_sync_to_google_sheet_with_docs_sheet_url_fails_gracefully(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SheetExporter(output_dir=tmpdir)
            success = exporter.sync_to_google_sheet(
                spreadsheet_id="https://docs.google.com/spreadsheets/d/1BxiMVs0XR.../edit",
                stops=[]
            )
            self.assertFalse(success)

    def test_sync_to_google_sheet_empty_webhook_skips(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = SheetExporter(output_dir=tmpdir)
            success = exporter.sync_to_google_sheet(
                spreadsheet_id="",
                stops=[]
            )
            self.assertFalse(success)

class TestOriginResolutionAndGeofencing(unittest.TestCase):
    def test_resolve_origin_coords_string(self):
        res = resolve_origin_input("43.7615, -79.4111")
        self.assertAlmostEqual(res["latitude"], 43.7615, places=4)
        self.assertAlmostEqual(res["longitude"], -79.4111, places=4)

    def test_resolve_origin_google_maps_desktop_url(self):
        url = "https://www.google.com/maps/place/Fairview+Mall/@43.7780,-79.3440,17z/data=!3m1!4b1"
        res = resolve_origin_input(url)
        self.assertAlmostEqual(res["latitude"], 43.7780, places=4)
        self.assertAlmostEqual(res["longitude"], -79.3440, places=4)
        self.assertEqual(res["name"], "Fairview Mall")

    def test_resolve_origin_google_maps_query_url(self):
        url = "https://maps.google.com/?q=43.8561,-79.3370"
        res = resolve_origin_input(url)
        self.assertAlmostEqual(res["latitude"], 43.8561, places=4)
        self.assertAlmostEqual(res["longitude"], -79.3370, places=4)

    def test_resolve_origin_preset_landmark(self):
        res = resolve_origin_input("Fairview Mall")
        self.assertAlmostEqual(res["latitude"], 43.7780, places=4)
        self.assertAlmostEqual(res["longitude"], -79.3440, places=4)

    def test_resolve_origin_none_default(self):
        res = resolve_origin_input(None)
        self.assertEqual(res["name"], "Field Operations Base")
        self.assertEqual(res["address"], "North York, Toronto, ON")
        self.assertAlmostEqual(res["latitude"], 43.7615, places=4)
        self.assertAlmostEqual(res["longitude"], -79.4111, places=4)

    def test_geofencing_exclude_regions(self):
        r_filter = RestaurantFilter(exclude_regions=["scarborough", "downtown"])
        cand_in_scarborough = {
            "name": "Popeyes Louisiana Kitchen",
            "address": "85 Ellesmere Rd Unit H, Scarborough, ON M1R 4C1 Canada",
            "region": "Scarborough"
        }
        cand_in_markham = {
            "name": "Popeyes Markham",
            "address": "8500 Woodbine Ave, Markham, ON L3R 4X9 Canada",
            "region": "Markham"
        }
        is_ex, reason = r_filter.is_region_excluded(cand_in_scarborough)
        self.assertTrue(is_ex)
        self.assertIn("scarborough", reason)

        is_ex2, _ = r_filter.is_region_excluded(cand_in_markham)
        self.assertFalse(is_ex2)

    def test_geofencing_include_regions(self):
        r_filter = RestaurantFilter(include_regions=["markham"])
        cand_markham = {"name": "Test A", "address": "123 Highway 7, Markham, ON"}
        cand_downtown = {"name": "Test B", "address": "100 Queen St W, Toronto, ON"}

        is_ex_markham, _ = r_filter.is_region_excluded(cand_markham)
        is_ex_downtown, reason = r_filter.is_region_excluded(cand_downtown)

        self.assertFalse(is_ex_markham)
        self.assertTrue(is_ex_downtown)
        self.assertIn("区域限定", reason)

    def test_radial_probe_and_routing(self):
        origin = {"latitude": 43.7780, "longitude": -79.3440, "name": "Fairview Mall"}
        probes = generate_radial_probe_points(origin["latitude"], origin["longitude"], radius_km=5.0)
        self.assertGreaterEqual(len(probes), 5)

        router = DirectionalRouter(origin=origin, bearing_degrees=0.0, max_depth_km=6.0)
        candidates = [
            {"name": "Stop Near 1", "latitude": 43.7800, "longitude": -79.3400},
            {"name": "Stop Far Away", "latitude": 44.5000, "longitude": -79.0000},
            {"name": "Stop Near 2", "latitude": 43.7750, "longitude": -79.3500},
        ]
        radial_route = router.plan_radial_route(candidates, target_count=2)
        self.assertEqual(len(radial_route), 2)
        names = [r["name"] for r in radial_route]
        self.assertNotIn("Stop Far Away", names)

class TestAdHocRestaurantExclusionAndPinning(unittest.TestCase):
    def test_adhoc_restaurant_exclusion(self):
        r_filter = RestaurantFilter(exclude_restaurants=["popeyes", "kfc"])
        cand1 = {"name": "Popeyes Louisiana Kitchen", "placeId": "ChIJ1"}
        cand2 = {"name": "bb.q Chicken Don Mills", "placeId": "ChIJ2"}

        is_ex1, reason1 = r_filter.is_manually_excluded(cand1)
        is_ex2, _ = r_filter.is_manually_excluded(cand2)

        self.assertTrue(is_ex1)
        self.assertIn("popeyes", reason1)
        self.assertFalse(is_ex2)

    def test_mandatory_restaurant_pinning_overrides_crm(self):
        import tempfile
        import json
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([
                {"name": "Kate & Jan Hotdogs", "placeId": "ChIJ4csnNTPV1IkRanjeCREz1WY", "phone": "9055550101"}
            ], f)
            tmp_path = f.name
        try:
            r_filter = RestaurantFilter(
                contracted_path=tmp_path,
                mandatory_restaurants=["kate & jan hotdogs"]
            )
            cand = {
                "name": "Kate & Jan Hotdogs",
                "placeId": "ChIJ4csnNTPV1IkRanjeCREz1WY",
                "phone": "9055550101"
            }
            accepted, stats = r_filter.filter_restaurants([cand])
            self.assertEqual(len(accepted), 1)
            self.assertTrue(accepted[0].get("_is_pinned"))
            self.assertEqual(stats["mandatory_count"], 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_router_guarantees_pinned_stops(self):
        origin = {"latitude": 43.7615, "longitude": -79.4111, "name": "Base"}
        router = DirectionalRouter(origin=origin, bearing_degrees=90.0, max_depth_km=30.0)
        candidates = []
        for i in range(10):
            cand = {
                "name": f"Stop {i}",
                "latitude": 43.7615,
                "longitude": -79.4111 + (i * 0.02),
                "placeId": f"p_{i}"
            }
            if i == 8:
                cand["_is_pinned"] = True
                cand["name"] = "Must Visit Pinned Stop"
            candidates.append(cand)

        route = router.plan_unidirectional_route(candidates, target_count=3)
        self.assertEqual(len(route), 3)
        names = [r["name"] for r in route]
        self.assertIn("Must Visit Pinned Stop", names)

    def test_check_restaurant_business_status_closed(self):
        from opening_hours import check_restaurant_open_status
        cand_perm = {"name": "Old Joint", "business_status": "CLOSED_PERMANENTLY"}
        is_open, reason = check_restaurant_open_status(cand_perm, "2026-09-21")
        self.assertFalse(is_open)
        self.assertIn("CLOSED_PERMANENTLY", reason)

        cand_temp = {"name": "Renovating Joint", "businessStatus": "CLOSED_TEMPORARILY"}
        is_open, reason = check_restaurant_open_status(cand_temp, "2026-09-21")
        self.assertFalse(is_open)
        self.assertIn("CLOSED_TEMPORARILY", reason)

    def test_check_restaurant_day_of_week_rest_day(self):
        from opening_hours import check_restaurant_open_status
        # 2026-09-21 is Monday, 2026-09-22 is Tuesday
        cand = {
            "name": "Monday Off Chicken",
            "openingHours": "11:30 AM – 9:00 PM (Mon Closed)",
            "regularOpeningHours": {
                "weekdayDescriptions": [
                    "Monday: Closed",
                    "Tuesday: 11:30 AM – 9:00 PM",
                    "Wednesday: 11:30 AM – 9:00 PM"
                ]
            }
        }
        # Monday visit: should be excluded
        is_open_mon, reason_mon = check_restaurant_open_status(cand, visit_date_str="2026-09-21")
        self.assertFalse(is_open_mon)
        self.assertIn("Closed", reason_mon)

        # Tuesday visit: should be accepted
        is_open_tue, _ = check_restaurant_open_status(cand, visit_date_str="2026-09-22")
        self.assertTrue(is_open_tue)

    def test_check_restaurant_dinner_only_late_opening(self):
        from opening_hours import check_restaurant_open_status
        cand_bar = {
            "name": "Night Pub & Wings",
            "openingHours": "5:30 PM – 3:00 AM",
            "regularOpeningHours": {
                "weekdayDescriptions": [
                    "Monday: 5:30 PM – 3:00 AM"
                ]
            }
        }
        # Default daytime sales run: exclude dinner only
        is_open, reason = check_restaurant_open_status(cand_bar, "2026-09-21", allow_dinner_only=False)
        self.assertFalse(is_open)
        self.assertIn("仅晚间夜宵营业", reason)

        # Evening run: permit dinner only
        is_open_allowed, _ = check_restaurant_open_status(cand_bar, "2026-09-21", allow_dinner_only=True)
        self.assertTrue(is_open_allowed)

    def test_filter_restaurants_excludes_closed_and_preserves_pinned(self):
        r_filter = RestaurantFilter(
            visit_date="2026-09-21", # Monday
            filter_closed=True,
            mandatory_restaurants=["pinned closed restaurant"]
        )
        candidates = [
            {
                "name": "Monday Closed Store",
                "openingHours": "11:00 AM – 9:00 PM (Mon Closed)",
                "placeId": "p_closed"
            },
            {
                "name": "Pinned Closed Restaurant",
                "openingHours": "11:00 AM – 9:00 PM (Mon Closed)",
                "placeId": "p_pinned"
            },
            {
                "name": "Regular Open Store",
                "openingHours": "11:00 AM – 9:00 PM",
                "placeId": "p_open"
            }
        ]
        accepted, stats = r_filter.filter_restaurants(candidates)
        accepted_pids = [r["placeId"] for r in accepted]
        self.assertNotIn("p_closed", accepted_pids)
        self.assertIn("p_open", accepted_pids)
        self.assertIn("p_pinned", accepted_pids)
        self.assertEqual(stats["excluded_closed"], 1)
        self.assertEqual(stats["mandatory_count"], 1)

    def test_google_maps_url_sanitizes_slashes_and_dummy_origin(self):
        from route_generator import RouteGenerator
        rg = RouteGenerator(
            origin_name="Field Operations Base",
            origin_address="North York, Toronto, ON",
            origin_lat=43.7615,
            origin_lng=-79.4111
        )
        stops = [
            {
                "name": "bb.q Chicken - Sheppard/Hmart",
                "address": "4885 Yonge St, North York, ON M2N 5N4"
            }
        ]
        _, master_url, _ = rg.process_route(stops, visit_date_str="2026-09-21")
        # 1. Dummy 'Field Operations Base' should be stripped from geocoding to prevent US Bryant Park match
        self.assertNotIn("Field%20Operations%20Base", master_url)
        self.assertIn("North%20York%2C%20Toronto%2C%20ON", master_url)
        # 2. Slash inside restaurant name should be replaced with - so it does not split into 2 stops
        self.assertNotIn("Sheppard/Hmart", master_url)
        self.assertIn("Sheppard%20-%20Hmart", master_url)
        # 3. Explicit Canada scope
        self.assertIn("Canada", master_url)

    def test_places_searcher_requires_api_key_and_parses_response(self):
        import json
        from unittest.mock import patch, MagicMock
        from places_searcher import PlacesSearcher

        # 1. Verification: without API key, search raises RuntimeError (mock mode completely eliminated)
        searcher_no_key = PlacesSearcher(api_key="")
        self.assertFalse(searcher_no_key.is_live_api_ready())
        with self.assertRaises(RuntimeError):
            searcher_no_key.search_corridor_probes([(43.7764, -79.2318)], keywords=["fried chicken"])

        # 2. Verification: with API key and standard HTTP response, correctly parses places
        searcher = PlacesSearcher(api_key="AIzaSy_TEST_KEY")
        self.assertTrue(searcher.is_live_api_ready())

        mock_api_response = {
            "places": [
                {
                    "id": "ChIJ_TEST_01",
                    "displayName": {"text": "Golden Fried Chicken"},
                    "formattedAddress": "123 Finch Ave E, Toronto, ON M2N 4R7, Canada",
                    "nationalPhoneNumber": "(416) 123-4567",
                    "location": {"latitude": 43.7780, "longitude": -79.4100},
                    "rating": 4.5,
                    "userRatingCount": 120,
                    "primaryType": "chicken_restaurant",
                    "businessStatus": "OPERATIONAL",
                    "regularOpeningHours": {
                        "weekdayDescriptions": [
                            "Monday: 11:00 AM – 10:00 PM",
                            "Tuesday: 11:00 AM – 10:00 PM",
                            "Wednesday: 11:00 AM – 10:00 PM",
                            "Thursday: 11:00 AM – 10:00 PM",
                            "Friday: 11:00 AM – 11:00 PM",
                            "Saturday: 11:00 AM – 11:00 PM",
                            "Sunday: 11:00 AM – 10:00 PM"
                        ]
                    }
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            results = searcher.search_corridor_probes([(43.7764, -79.2318)], keywords=["fried chicken"])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["name"], "Golden Fried Chicken")
            self.assertEqual(results[0]["placeId"], "ChIJ_TEST_01")
            self.assertEqual(results[0]["phone"], "(416) 123-4567")

    def test_non_interactive_config_updater(self):
        import tempfile
        from config_manager import update_config_values, get_config_summary
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            temp_cfg_path = tf.name

        try:
            # 1. Update values non-interactively
            cfg = update_config_values(
                api_key="AIzaSyUnitTestKey123456",
                sheet_url="https://docs.google.com/spreadsheets/d/1BxiMVs0XRTestSpreadsheetId99/edit",
                origin_input="Fairview Mall",
                config_save_path=temp_cfg_path
            )
            self.assertEqual(cfg["google_maps_api_key"], "AIzaSyUnitTestKey123456")
            self.assertEqual(cfg["google_sheets"]["spreadsheet_id"], "1BxiMVs0XRTestSpreadsheetId99")
            self.assertEqual(cfg["default_origin"]["name"], "Fairview Mall")

            # 2. Verify get_config_summary output
            summary = get_config_summary(temp_cfg_path)
            self.assertTrue(summary["api_key_configured"])
            self.assertIn("...", summary["api_key_masked"])
            self.assertTrue(summary["google_sheet_configured"])
            self.assertEqual(summary["google_sheet_id"], "1BxiMVs0XRTestSpreadsheetId99")
            self.assertEqual(summary["default_origin"]["name"], "Fairview Mall")
        finally:
            if os.path.exists(temp_cfg_path):
                os.remove(temp_cfg_path)

    def test_project_root_detection_and_path_isolation(self):
        import tempfile
        from config_manager import find_project_root, get_effective_storage_paths

        # 1. Test when inside a dedicated project directory with .git or pyproject.toml
        current_root = find_project_root()
        self.assertIsNotNone(current_root)
        self.assertTrue(os.path.exists(current_root))

        paths = get_effective_storage_paths()
        self.assertTrue(paths["is_project_mode"])
        self.assertTrue(paths["config_file"].startswith(current_root))
        self.assertTrue(paths["temp_dir"].startswith(current_root))
        self.assertTrue(paths["output_dir"].startswith(current_root))

        # 2. Test simulating an installation inside user home (~/.gemini/skills/...)
        home_dir = os.path.realpath(os.path.expanduser("~"))
        simulated_home_skill = os.path.join(home_dir, ".gemini", "skills", "daily-restaurant-lead-scout")
        simulated_root = find_project_root(start_dir=simulated_home_skill)
        # Should NOT treat ~ as a project root
        self.assertNotEqual(simulated_root, home_dir)

        # 3. Test simulating Antigravity workspace structure (<workspace>/.agents/skills/<name>)
        fake_workspace = "/fake/workspace/my_project"
        simulated_agents_skill = os.path.join(fake_workspace, ".agents", "skills", "daily-restaurant-lead-scout")
        detected_root = find_project_root(start_dir=simulated_agents_skill)
        self.assertEqual(detected_root, fake_workspace)

        # 4. Test simulating nested wrappers (<workspace>/.gemini/antigravity/skills/<name>)
        simulated_nested_skill = os.path.join(fake_workspace, ".gemini", "antigravity", "skills", "daily-restaurant-lead-scout")
        detected_nested_root = find_project_root(start_dir=simulated_nested_skill)
        self.assertEqual(detected_nested_root, fake_workspace)

        # 5. Test simulating global install inside ~/.agents/skills (should NOT treat home as project root)
        simulated_global_agents = os.path.join(home_dir, ".agents", "skills", "daily-restaurant-lead-scout")
        self.assertNotEqual(find_project_root(start_dir=simulated_global_agents), home_dir)

class TestJevIntegration(unittest.TestCase):
    def setUp(self):
        from jev_client import JevDecisionClient
        self.client = JevDecisionClient(api_key="sk-or-v1-mockmockmockmockmock123")

    def test_client_readiness(self):
        from jev_client import JevDecisionClient
        c_valid = JevDecisionClient(api_key="sk-or-v1-1234567890abcdef")
        self.assertTrue(c_valid.is_ready())

        c_empty = JevDecisionClient(api_key="")
        self.assertFalse(c_empty.is_ready())

        c_placeholder = JevDecisionClient(api_key="YOUR_OPENROUTER_API_KEY")
        self.assertFalse(c_placeholder.is_ready())

    def test_config_manager_openrouter_support(self):
        import tempfile
        from config_manager import update_config_values, get_config_summary

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_cfg = f.name

        try:
            update_config_values(
                config_save_path=temp_cfg,
                openrouter_key="sk-or-v1-testkey1234567890"
            )
            summary = get_config_summary(temp_cfg)
            self.assertTrue(summary["openrouter_api_key_configured"])
            self.assertIn("sk-or-v1", summary["openrouter_key_masked"])
        finally:
            if os.path.exists(temp_cfg):
                os.remove(temp_cfg)

    def test_typesafe_official_endpoint_and_defaults(self):
        from jev_client import JevDecisionClient
        client = JevDecisionClient(api_key="typesafe-test-key-12345678")
        self.assertEqual(client.api_url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(client.model, "jev-latest")
        self.assertTrue(client.is_ready())

    def test_typesafe_request_formatting(self):
        from unittest.mock import patch, MagicMock
        from jev_client import JevDecisionClient
        import json
        import io

        client = JevDecisionClient(api_key="typesafe-test-key-12345678")
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "answers": {
                "is_urgent": {"noul": 0.95}
            }
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            answers = client.query_decisions("test state", {"is_urgent": {"type": "noul"}})
            self.assertIsNotNone(answers)
            self.assertEqual(answers["is_urgent"]["noul"], 0.95)

            # Verify the request object sent to urlopen
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, "https://api.typesafe.ai/v1/systemone")
            self.assertEqual(req.get_header("Authorization"), "Bearer typesafe-test-key-12345678")
            self.assertEqual(req.get_header("Content-type"), "application/json")
            req_body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(req_body["model"], "jev-latest")
            self.assertEqual(req_body["state"], "test state")

    def test_config_manager_typesafe_support(self):
        import tempfile
        from config_manager import update_config_values, get_config_summary

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            temp_cfg = f.name

        try:
            update_config_values(
                config_save_path=temp_cfg,
                typesafe_key="typesafe_secret_key_12345678"
            )
            summary = get_config_summary(temp_cfg)
            self.assertTrue(summary["typesafe_api_key_configured"])
            self.assertIn("typesafe", summary["typesafe_key_masked"])
        finally:
            if os.path.exists(temp_cfg):
                os.remove(temp_cfg)

    def test_mock_jev_entity_match_same_store(self):
        # Mock client query_decisions returning same_store
        class MockSameStoreJev:
            def is_ready(self):
                return True
            def evaluate_entity_match(self, candidate, reference):
                return True, 0.96, "Jev 判定为同一实体", "same_store"

        cand = {"name": "Old Place Wings", "address": "100 Highway 7", "phone": "9051234567"}
        ref_list = [{"name": "Old Place Restaurant", "address": "100 Hwy 7", "phone": "9051234567"}]

        is_match, conf, rationale, matched_ref = semantic_entity_match(cand, ref_list, jev_client=MockSameStoreJev())
        self.assertTrue(is_match)
        self.assertGreaterEqual(conf, 0.70)
        self.assertIn("Jev", rationale)

    def test_mock_jev_entity_match_chain_different_branch(self):
        # Mock client recognizing same brand but different branch -> should not exclude
        class MockChainBranchJev:
            def is_ready(self):
                return True
            def evaluate_entity_match(self, candidate, reference):
                return False, 0.15, "同连锁品牌不同分店", "chain_different_branch"

        cand = {"name": "Popeyes Louisiana Kitchen", "address": "500 Yonge St"}
        ref_list = [{"name": "Popeyes", "address": "100 Sheppard Ave"}]

        is_match, conf, rationale, matched_ref = semantic_entity_match(cand, ref_list, jev_client=MockChainBranchJev())
        self.assertFalse(is_match)

    def test_mock_jev_fried_food_audit(self):
        from fried_model_auditor import AgentAuditManager

        class MockFriedJev:
            def is_ready(self):
                return True
            def evaluate_fried_food(self, place):
                return True, 0.92, ["炸鸡/鸡翅 (Fried Chicken/Wings)"], "Jev 确定为炸鸡专营店"

        mgr = AgentAuditManager(jev_client=MockFriedJev())
        place = {
            "id": "mock_p1",
            "name": "Super Hot Wings",
            "primaryType": "chicken_restaurant",
            "categories": ["restaurant"],
            "editorialSummary": "Known for crisp wings and fries."
        }
        is_fried, conf, dishes, rat = mgr.audit_restaurant(place)
        self.assertTrue(is_fried)
        self.assertGreaterEqual(conf, 0.9)
        self.assertIn("Jev", rat)

    def test_graceful_fallback_when_jev_offline(self):
        from fried_model_auditor import AgentAuditManager

        class MockFailingJev:
            def is_ready(self):
                return True
            def evaluate_fried_food(self, place):
                return None  # simulates HTTP failure

        mgr = AgentAuditManager(jev_client=MockFailingJev())
        place = {
            "id": "mock_p2",
            "name": "Golden Fish and Chips",
            "primaryType": "seafood_restaurant",
            "editorialSummary": "Serving fresh halibut and chips daily."
        }
        # Falls back to local regex heuristic and still succeeds
        is_fried, conf, dishes, rat = mgr.audit_restaurant(place)
        self.assertTrue(is_fried)
        self.assertIn("fish and chips", dishes)

    def test_jev_tier_classification(self):
        from jev_client import classify_jev_tier, JevFriedResult

        # High confidence positive -> DEFINITIVE_PASS
        tier1 = classify_jev_tier(0.92, "fried_chicken", 2)
        self.assertEqual(tier1, "DEFINITIVE_PASS")

        # Low confidence negative -> DEFINITIVE_REJECT
        tier2 = classify_jev_tier(0.15, "non_fried", 0)
        self.assertEqual(tier2, "DEFINITIVE_REJECT")

        # Ambiguous / Borderline -> NEED_MULTIMODAL_INSPECTION
        tier3 = classify_jev_tier(0.55, "asian_fried", 1)
        self.assertEqual(tier3, "NEED_MULTIMODAL_INSPECTION")

        # Test JevFriedResult backward compatibility
        res = JevFriedResult(True, 0.92, ["Fried Chicken"], "High confidence", tier_status=tier1)
        # Unpacks as 4 elements
        is_f, c, d, r = res
        self.assertTrue(is_f)
        self.assertEqual(c, 0.92)
        self.assertEqual(res.tier_status, "DEFINITIVE_PASS")

    def test_agent_native_export_pending_audit_only_ambiguous(self):
        import tempfile
        from fried_model_auditor import AgentAuditManager
        from jev_client import JevFriedResult

        temp_dir = tempfile.mkdtemp()

        class MockTieredJev:
            def is_ready(self):
                return True
            def evaluate_fried_food(self, place):
                if "popeyes" in place.get("name", "").lower():
                    return JevFriedResult(True, 0.98, ["Fried Chicken"], "Clear pass", tier_status="DEFINITIVE_PASS")
                elif "juice" in place.get("name", "").lower():
                    return JevFriedResult(False, 0.10, ["Non-fried"], "Clear reject", tier_status="DEFINITIVE_REJECT")
                else:
                    return JevFriedResult(True, 0.55, ["Asian Fried"], "Ambiguous", tier_status="NEED_MULTIMODAL_INSPECTION")

        mgr = AgentAuditManager(audit_dir=temp_dir, jev_client=MockTieredJev())

        candidates = [
            {"id": "p1", "placeId": "p1", "name": "Popeyes", "primaryType": "fast_food_restaurant"},
            {"id": "p2", "placeId": "p2", "name": "Fresh Juice Bar", "primaryType": "juice_shop"},
            {"id": "p3", "placeId": "p3", "name": "Kyoto Izakaya", "primaryType": "japanese_restaurant", "photo_urls": []}
        ]

        pending_file = mgr.export_pending_audit(candidates, download_images=False)
        ambiguous = mgr.ambiguous_candidates
        # Only Kyoto Izakaya should be exported to pending_agent_audit.json!
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous[0]["placeId"], "p3")
        self.assertEqual(ambiguous[0]["name"], "Kyoto Izakaya")

        # Verify Popeyes passes directly and Juice Bar is rejected
        is_f1, _, _, _ = mgr.audit_restaurant(candidates[0])
        self.assertTrue(is_f1)
        is_f2, _, _, _ = mgr.audit_restaurant(candidates[1])
        self.assertFalse(is_f2)

    def test_agent_multimodal_decision_priority(self):
        import tempfile, json
        from fried_model_auditor import AgentAuditManager

        temp_dir = tempfile.mkdtemp()
        results_file = os.path.join(temp_dir, "agent_audit_results.json")
        # Pre-populate agent audit results
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump([
                {
                    "placeId": "izakaya_99",
                    "is_fried": True,
                    "fried_dishes": ["tempura", "karaage"],
                    "rationale": "Verified by Agent multimodal image inspection: visible fry basket"
                }
            ], f)

        mgr = AgentAuditManager(audit_dir=temp_dir)
        candidate = {
            "id": "izakaya_99",
            "name": "Izakaya 99",
            "primaryType": "japanese_restaurant"
        }

        is_f, conf, dishes, rat = mgr.audit_restaurant(candidate)
        self.assertTrue(is_f)
        self.assertEqual(conf, 1.0)
        self.assertIn("tempura", dishes)
        self.assertIn("Verified by Agent multimodal", rat)
        self.assertEqual(mgr.stats["agent_cached"], 1)

class TestDecoupledSearchAndBugFixes(unittest.TestCase):
    def test_radial_probe_points_small_radius_deploys_9_probes(self):
        from directional_router import generate_radial_probe_points
        probes = generate_radial_probe_points(43.8682, -79.2883, radius_km=1.5)
        self.assertEqual(len(probes), 9)
        # Verify center probe
        self.assertEqual(probes[0], (43.8682, -79.2883))

    def test_landmark_matching_does_not_hijack_detailed_address(self):
        from config_manager import resolve_origin_input
        # "markham" exact matches preset
        res_city = resolve_origin_input("markham")
        self.assertEqual(res_city["name"], "Markham")

        # "CF Markville, 5000 Hwy 7, Markham, ON" should NOT be hijacked by "markham" preset
        res_specific = resolve_origin_input("CF Markville, 5000 Hwy 7, Markham, ON")
        # When no active Google API key, it falls back to raw place query name instead of Markham city center
        self.assertNotEqual(res_specific["name"], "Markham")

    def test_places_searcher_probe_query_suffix_deduplication(self):
        from places_searcher import PlacesSearcher
        searcher = PlacesSearcher(api_key="TEST_MOCK_KEY")
        # Ensure query suffix logic avoids repetition
        kw_restaurant = "asian restaurant"
        kw_clean = kw_restaurant.strip()
        kw_lower = kw_clean.lower()
        has_suffix = any(term in kw_lower for term in ["restaurant", "food", "court", "dining", "kitchen", "cafe", "bakery", "eatery", "bar"])
        query = kw_clean if has_suffix else f"{kw_clean} restaurant"
        self.assertEqual(query, "asian restaurant")

        kw_plain = "fried chicken"
        kw_clean = kw_plain.strip()
        kw_lower = kw_clean.lower()
        has_suffix = any(term in kw_lower for term in ["restaurant", "food", "court", "dining", "kitchen", "cafe", "bakery", "eatery", "bar"])
        query = kw_clean if has_suffix else f"{kw_clean} restaurant"
        self.assertEqual(query, "fried chicken restaurant")

if __name__ == "__main__":
    unittest.main()


