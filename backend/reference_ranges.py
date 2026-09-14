"""
Reference range database for common lab tests.

normal_low / normal_high  = standard adult reference interval (used when
  sex is unknown, or for tests without a clinically meaningful sex split)
sex_ranges = optional per-sex override, used when the file provides a
  Sex/Gender column AND this test has a real, recognized difference
  between male and female ranges (e.g. Hemoglobin, Creatinine).
plausible_low / plausible_high = physiologically possible bounds
  (values outside this are almost certainly data-entry/instrument errors,
  not real patient results)

NOTE: These are general-purpose teaching/demo values, NOT calibrated for
clinical use. Before any real deployment, replace these with ranges
signed off by a qualified pathologist / your lab's SOP, and add
age/pregnancy-adjusted ranges too.
"""

REFERENCE_RANGES = {
    "hemoglobin": {
        "aliases": ["hb", "hgb", "haemoglobin"],
        "unit": "g/dL",
        "normal_low": 12.0, "normal_high": 17.5,
        "plausible_low": 2.0, "plausible_high": 24.0,
        "sex_ranges": {
            "M": {"normal_low": 13.5, "normal_high": 17.5},
            "F": {"normal_low": 12.0, "normal_high": 15.5},
        },
    },
    "wbc": {
        "aliases": ["white blood cell count", "leukocyte count", "tlc"],
        "unit": "x10^3/uL",
        "normal_low": 4.0, "normal_high": 11.0,
        "plausible_low": 0.1, "plausible_high": 100.0,
    },
    "platelet count": {
        "aliases": ["platelets", "plt"],
        "unit": "x10^3/uL",
        "normal_low": 150, "normal_high": 450,
        "plausible_low": 5, "plausible_high": 1500,
    },
    "glucose": {
        "aliases": ["blood glucose", "fbs", "fasting blood sugar", "rbs"],
        "unit": "mg/dL",
        "normal_low": 70, "normal_high": 140,
        "plausible_low": 10, "plausible_high": 900,
    },
    "creatinine": {
        "aliases": ["serum creatinine", "creat"],
        "unit": "mg/dL",
        "normal_low": 0.6, "normal_high": 1.3,
        "plausible_low": 0.1, "plausible_high": 20.0,
        "sex_ranges": {
            "M": {"normal_low": 0.7, "normal_high": 1.3},
            "F": {"normal_low": 0.6, "normal_high": 1.1},
        },
    },
    "total cholesterol": {
        "aliases": ["cholesterol"],
        "unit": "mg/dL",
        "normal_low": 125, "normal_high": 200,
        "plausible_low": 40, "plausible_high": 600,
    },
    "sodium": {
        "aliases": ["na", "serum sodium"],
        "unit": "mmol/L",
        "normal_low": 135, "normal_high": 145,
        "plausible_low": 100, "plausible_high": 180,
    },
    "potassium": {
        "aliases": ["k", "serum potassium"],
        "unit": "mmol/L",
        "normal_low": 3.5, "normal_high": 5.1,
        "plausible_low": 1.5, "plausible_high": 9.0,
    },
    "urea": {
        "aliases": ["blood urea", "bun"],
        "unit": "mg/dL",
        "normal_low": 7, "normal_high": 20,
        "plausible_low": 1, "plausible_high": 200,
    },
    "tsh": {
        "aliases": ["thyroid stimulating hormone"],
        "unit": "uIU/mL",
        "normal_low": 0.4, "normal_high": 4.0,
        "plausible_low": 0.001, "plausible_high": 100,
    },
    "alt": {
        "aliases": ["sgpt", "alanine aminotransferase"],
        "unit": "U/L",
        "normal_low": 7, "normal_high": 56,
        "plausible_low": 1, "plausible_high": 3000,
        "sex_ranges": {
            "M": {"normal_low": 10, "normal_high": 56},
            "F": {"normal_low": 7, "normal_high": 45},
        },
    },
    "ast": {
        "aliases": ["sgot", "aspartate aminotransferase"],
        "unit": "U/L",
        "normal_low": 10, "normal_high": 40,
        "plausible_low": 1, "plausible_high": 3000,
    },
}

# Build a flat lookup: alias -> canonical test key
_ALIAS_LOOKUP = {}
for key, profile in REFERENCE_RANGES.items():
    _ALIAS_LOOKUP[key] = key
    for alias in profile["aliases"]:
        _ALIAS_LOOKUP[alias] = key


def _normalize_sex(raw_sex):
    if not raw_sex:
        return None
    s = str(raw_sex).strip().upper()
    if s in ("M", "MALE"):
        return "M"
    if s in ("F", "FEMALE"):
        return "F"
    return None


def find_test_profile(raw_name: str, sex: str = None):
    if not raw_name:
        return None
    cleaned = raw_name.strip().lower()
    canonical = _ALIAS_LOOKUP.get(cleaned)
    if not canonical:
        # loose contains-match as a fallback (handles "Hemoglobin (Hb)" etc.)
        for alias, key in _ALIAS_LOOKUP.items():
            if alias in cleaned or cleaned in alias:
                canonical = key
                break
    if not canonical:
        return None

    profile = REFERENCE_RANGES[canonical]
    lo, hi = profile["normal_low"], profile["normal_high"]
    used_sex_range = False

    norm_sex = _normalize_sex(sex)
    sex_ranges = profile.get("sex_ranges")
    if norm_sex and sex_ranges and norm_sex in sex_ranges:
        lo = sex_ranges[norm_sex]["normal_low"]
        hi = sex_ranges[norm_sex]["normal_high"]
        used_sex_range = True

    return {
        "name": canonical, "unit": profile["unit"],
        "normal_low": lo, "normal_high": hi,
        "plausible_low": profile["plausible_low"], "plausible_high": profile["plausible_high"],
        "used_sex_specific_range": used_sex_range,
    }
