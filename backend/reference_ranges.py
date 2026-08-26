"""
Reference range database for common lab tests.
normal_low / normal_high  = standard adult reference interval
plausible_low / plausible_high = physiologically possible bounds
  (values outside this are almost certainly data-entry/instrument errors,
  not real patient results)

NOTE: These are general-purpose teaching/demo values, NOT calibrated for
clinical use. Before any real deployment, replace these with ranges
signed off by a qualified pathologist / your lab's SOP, and add
age/sex/pregnancy-adjusted ranges.
"""

REFERENCE_RANGES = {
    "hemoglobin": {
        "aliases": ["hb", "hgb", "haemoglobin"],
        "unit": "g/dL",
        "normal_low": 12.0, "normal_high": 17.5,
        "plausible_low": 2.0, "plausible_high": 24.0,
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


def find_test_profile(raw_name: str):
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
    return {"name": canonical, "unit": profile["unit"],
            "normal_low": profile["normal_low"], "normal_high": profile["normal_high"],
            "plausible_low": profile["plausible_low"], "plausible_high": profile["plausible_high"]}
