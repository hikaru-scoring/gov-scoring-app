# test_scoring.py
"""Pytest suite for the GOV-1000 scoring platform.

Covers state scoring, municipal scoring, agency data structures,
and data file integrity.
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock streamlit before importing any project modules
# ---------------------------------------------------------------------------
_st_mock = MagicMock()


def _passthrough_decorator(*args, **kwargs):
    """Return a no-op decorator that passes the function through unchanged."""
    def decorator(fn):
        return fn
    if args and callable(args[0]):
        return args[0]
    return decorator


_st_mock.cache_data = _passthrough_decorator
sys.modules["streamlit"] = _st_mock

# Set working directory so relative paths (census_data/) resolve correctly
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# Now safe to import project modules
from state_data import _clamp as state_clamp, score_state, STATES, STATE_AXES_LABELS
from municipal_data import (
    _clamp as muni_clamp,
    parse_pid_file,
    parse_finance_file,
    score_municipality,
    load_and_score_top_cities,
    DATA_FILES,
    MUNICIPAL_AXES_LABELS,
)
from data_logic import AGENCIES, AXES_LABELS


# ===================================================================
# 1. State scoring logic (state_data.py)
# ===================================================================

class TestStateClamp:
    def test_within_range(self):
        assert state_clamp(100) == 100

    def test_below_zero(self):
        assert state_clamp(-50) == 0

    def test_above_max(self):
        assert state_clamp(300) == 200

    def test_exact_zero(self):
        assert state_clamp(0) == 0

    def test_exact_max(self):
        assert state_clamp(200) == 200

    def test_returns_int(self):
        result = state_clamp(99.7)
        assert isinstance(result, int)


class TestScoreState:
    """Test score_state with synthetic finance data (no API calls)."""

    @pytest.fixture
    def base_finances(self):
        """Create a minimal finances dict for all states."""
        finances = {}
        for fips in STATES:
            finances[fips] = {
                "revenue": 50_000_000_000,
                "expenditure": 45_000_000_000,
                "debt": 10_000_000_000,
                "federal_revenue": 10_000_000_000,
                "interest": 2_000_000_000,
                "capital_outlay": 5_000_000_000,
                "cash_holdings": 20_000_000_000,
                "taxes": 30_000_000_000,
            }
        return finances

    def test_returns_correct_structure(self, base_finances):
        result = score_state("06", base_finances)
        assert result is not None
        for key in ("name", "abbr", "fips", "axes", "total",
                     "revenue", "expenditure", "debt"):
            assert key in result, f"Missing key: {key}"
        assert result["name"] == "California"
        assert result["abbr"] == "CA"
        assert result["fips"] == "06"

    def test_all_five_axes_present(self, base_finances):
        result = score_state("06", base_finances)
        assert len(result["axes"]) == 5
        for label in STATE_AXES_LABELS:
            assert label in result["axes"], f"Missing axis: {label}"

    def test_axes_within_range(self, base_finances):
        result = score_state("06", base_finances)
        for label, value in result["axes"].items():
            assert 0 <= value <= 200, f"{label} = {value} out of range"

    def test_total_is_sum_of_axes(self, base_finances):
        result = score_state("06", base_finances)
        assert result["total"] == sum(result["axes"].values())

    def test_total_within_range(self, base_finances):
        result = score_state("06", base_finances)
        assert 0 <= result["total"] <= 1000

    def test_invalid_fips_returns_none(self, base_finances):
        assert score_state("99", base_finances) is None

    def test_surplus_budget_balance_above_100(self):
        """A state with revenue > expenditure should get Budget Balance > 100."""
        finances = {
            "06": {
                "revenue": 100_000_000_000,
                "expenditure": 50_000_000_000,
                "debt": 0,
                "federal_revenue": 0,
                "interest": 0,
                "capital_outlay": 0,
                "cash_holdings": 0,
                "taxes": 0,
            }
        }
        result = score_state("06", finances)
        assert result["axes"]["Budget Balance"] > 100

    def test_zero_debt_gives_max_debt_burden(self):
        """A state with zero debt should get Debt Burden = 200."""
        finances = {
            "06": {
                "revenue": 100_000_000_000,
                "expenditure": 90_000_000_000,
                "debt": 0,
                "federal_revenue": 10_000_000_000,
                "interest": 1_000_000_000,
                "capital_outlay": 5_000_000_000,
                "cash_holdings": 10_000_000_000,
                "taxes": 50_000_000_000,
            }
        }
        result = score_state("06", finances)
        # debt_ratio = 0/revenue = 0, so ax2 = 200 - 0*100 = 200
        assert result["axes"]["Debt Burden"] == 200


# ===================================================================
# 2. Municipal scoring logic (municipal_data.py)
# ===================================================================

class TestMunicipalClamp:
    def test_within_range(self):
        assert muni_clamp(100.0) == 100.0

    def test_below_zero(self):
        assert muni_clamp(-50.0) == 0.0

    def test_above_max(self):
        assert muni_clamp(300.0) == 200.0


class TestParsePidFile:
    def test_returns_municipalities(self):
        pid = parse_pid_file(DATA_FILES[2022]["pid"])
        assert len(pid) > 0, "PID file should contain municipalities"
        # Every entry should have type_code "2"
        for gov_id, info in list(pid.items())[:20]:
            assert info["type_code"] == "2"

    def test_has_expected_fields(self):
        pid = parse_pid_file(DATA_FILES[2022]["pid"])
        sample = next(iter(pid.values()))
        for field in ("name", "state_fips", "type_code", "county",
                       "population", "fips_place"):
            assert field in sample, f"Missing field: {field}"


class TestParseFinanceFile:
    def test_returns_data_with_item_codes(self):
        fin = parse_finance_file(DATA_FILES[2022]["finance"])
        assert len(fin) > 0, "Finance file should contain records"
        # Check that item codes are 3-character strings
        sample_gov_id = next(iter(fin))
        items = fin[sample_gov_id]
        assert len(items) > 0
        for item_code in items:
            assert isinstance(item_code, str)
            assert len(item_code) == 3


class TestScoreMunicipality:
    @pytest.fixture
    def census_data(self):
        pid = parse_pid_file(DATA_FILES[2022]["pid"])
        fin = parse_finance_file(DATA_FILES[2022]["finance"])
        return pid, fin

    def test_returns_correct_structure(self, census_data):
        pid, fin = census_data
        # Pick the most populous municipality
        top_gov_id = max(pid, key=lambda k: pid[k]["population"])
        result = score_municipality(top_gov_id, pid, fin)
        assert result is not None
        for key in ("name", "gov_id", "state_fips", "state_abbr",
                     "population", "axes", "total", "revenue",
                     "expenditure", "taxes", "ig_revenue"):
            assert key in result, f"Missing key: {key}"

    def test_all_five_axes_present_and_in_range(self, census_data):
        pid, fin = census_data
        top_gov_id = max(pid, key=lambda k: pid[k]["population"])
        result = score_municipality(top_gov_id, pid, fin)
        assert len(result["axes"]) == 5
        for label in MUNICIPAL_AXES_LABELS:
            assert label in result["axes"], f"Missing axis: {label}"
        for label, value in result["axes"].items():
            assert 0 <= value <= 200, f"{label} = {value} out of range"

    def test_total_within_range(self, census_data):
        pid, fin = census_data
        top_gov_id = max(pid, key=lambda k: pid[k]["population"])
        result = score_municipality(top_gov_id, pid, fin)
        assert 0 <= result["total"] <= 1000

    def test_not_found_returns_none(self, census_data):
        pid, fin = census_data
        assert score_municipality("XXXXXXXXXXXX", pid, fin) is None


class TestLoadAndScoreTopCities:
    def test_returns_sorted_list(self):
        results = load_and_score_top_cities(n=10, year=2022)
        assert len(results) > 0
        assert len(results) <= 10
        # Verify sorted by total descending
        for i in range(len(results) - 1):
            assert results[i]["total"] >= results[i + 1]["total"]


# ===================================================================
# 3. Agency scoring (data_logic.py)
# ===================================================================

class TestAgencyData:
    def test_agencies_has_15_entries(self):
        assert len(AGENCIES) == 15

    def test_axes_labels_has_5_entries(self):
        assert len(AXES_LABELS) == 5

    def test_agency_entries_have_name_and_abbr(self):
        for code, info in AGENCIES.items():
            assert "name" in info
            assert "abbr" in info


# ===================================================================
# 4. Data integrity
# ===================================================================

class TestDataIntegrity:
    def test_census_data_files_exist_2022(self):
        for key, path in DATA_FILES[2022].items():
            full_path = os.path.join(PROJECT_DIR, path)
            assert os.path.isfile(full_path), f"Missing: {full_path}"

    def test_census_data_files_exist_2023(self):
        for key, path in DATA_FILES[2023].items():
            full_path = os.path.join(PROJECT_DIR, path)
            assert os.path.isfile(full_path), f"Missing: {full_path}"

    def test_state_ratings_json_exists_and_has_50_entries(self):
        path = os.path.join(PROJECT_DIR, "state_ratings.json")
        assert os.path.isfile(path), f"Missing: {path}"
        with open(path, "r") as f:
            data = json.load(f)
        assert len(data) == 50, f"Expected 50 entries, got {len(data)}"

    def test_gao_findings_json_exists(self):
        path = os.path.join(PROJECT_DIR, "gao_findings.json")
        assert os.path.isfile(path), f"Missing: {path}"
        with open(path, "r") as f:
            data = json.load(f)
        assert isinstance(data, dict)
