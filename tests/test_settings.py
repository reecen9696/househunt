"""Editing the scrape criteria from the review page.

config.yaml's comments are the tool's documentation, and the file is what
the weekly scan reads. So the bar for these tests is higher than "the value
changed": nothing outside the edited keys may move, and a rejected value
must leave the file exactly as it was.
"""
import pytest
import yaml

from passedin.settings import (
    FIELDS,
    PROPERTY_TYPES,
    SettingsError,
    read_settings,
    write_settings,
)

# A miniature of the real config: comments above a key, a comment *inside* a
# value (the suburb note), an empty flow list immediately followed by a
# section header, and unrelated sections either side.
CONFIG = """\
# ============================================================================
# Passed-In Property Finder — configuration
# ============================================================================

fetch:
  fetcher: scrapedo
  chrome:
    # Pin to your installed Chrome major version if auto-detection fails.
    version_main: null
    driver_path: null

filters:
  # Applied to the LOWER bound of a quoted range: vendors quote low.
  price_ceiling: 1200000
  # Optional second ceiling.
  stretch_ceiling: null

  suburbs_mode: list
  suburbs:
    # Names as they appear on the source sites.
    - Windsor
    - Richmond

  min_bedrooms: 2
  # Property types to INCLUDE. Empty list = all types.
  property_types:
    - House
  # Property types to exclude entirely (applied after the include list).
  exclude_property_types: []
  # Null land size means "unknown", and unknown must be INCLUDED.
  min_land_size_sqm: null
  exclude_agencies: []
  # Substring match against the street part of the address.
  exclude_streets: []

# ----------------------------------------------------------------------------
# Price enrichment: resolve a price signal for each non-sale.
# ----------------------------------------------------------------------------
enrich:
  enabled: true
  max_pages_per_run: 40
"""


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    return p


# --- the guarantee that matters ---------------------------------------------

def test_every_comment_survives_a_save(cfg):
    write_settings(cfg, {"price_ceiling": 1350000,
                         "suburbs": ["Windsor", "Armadale"],
                         "exclude_streets": "Bell St, Sydney Rd"})
    after = cfg.read_text()
    for comment in [line for line in CONFIG.splitlines()
                    if line.strip().startswith("#")]:
        assert comment in after, f"lost comment: {comment.strip()!r}"


def test_emptying_a_list_does_not_swallow_the_next_section(cfg):
    """`exclude_streets: []` is followed by a section header. Replacing the
    value with a block list and back again once ate that header entirely."""
    write_settings(cfg, {"exclude_streets": "Bell St"})
    write_settings(cfg, {"exclude_streets": []})
    after = cfg.read_text()
    assert "# Price enrichment: resolve a price signal for each non-sale." in after
    assert "enrich:" in after
    assert "max_pages_per_run: 40" in after


def test_a_full_round_trip_leaves_the_file_byte_identical(cfg):
    original = cfg.read_text()
    write_settings(cfg, {"price_ceiling": 1350000, "stretch_ceiling": 1500000,
                         "min_bedrooms": 3, "min_land_size_sqm": 250,
                         "property_types": ["House", "Townhouse"],
                         "suburbs": ["Armadale"],
                         "exclude_streets": "Bell St", "exclude_agencies": "Jellis"})
    assert cfg.read_text() != original
    write_settings(cfg, {"price_ceiling": 1200000, "stretch_ceiling": None,
                         "min_bedrooms": 2, "min_land_size_sqm": None,
                         "property_types": ["House"],
                         "suburbs": ["Windsor", "Richmond"],
                         "exclude_streets": [], "exclude_agencies": []})
    assert cfg.read_text() == original


def test_only_the_edited_lines_change(cfg):
    before = CONFIG.splitlines()
    write_settings(cfg, {"price_ceiling": 1350000})
    after = cfg.read_text().splitlines()
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1
    assert after[differing[0]].strip() == "price_ceiling: 1350000"


def test_comment_inside_a_value_is_carried_across(cfg):
    """The note above the suburb list documents that setting, so it has to
    move with it rather than being stranded or dropped."""
    write_settings(cfg, {"suburbs": ["Prahran"]})
    text = cfg.read_text()
    assert "    # Names as they appear on the source sites." in text
    assert text.index("# Names as they appear") < text.index("- Prahran")


def test_comment_after_a_value_stays_with_the_next_key(cfg):
    """The exclude_property_types note sits directly below the last
    property_types item; it must not be absorbed into that list."""
    write_settings(cfg, {"property_types": ["Unit"]})
    text = cfg.read_text()
    assert text.count("# Property types to exclude entirely") == 1
    assert (text.index("# Property types to exclude entirely")
            < text.index("exclude_property_types: []"))


# --- the file stays loadable and correct ------------------------------------

def test_values_land_where_the_scan_reads_them(cfg):
    write_settings(cfg, {"price_ceiling": 950000, "min_bedrooms": 4,
                         "suburbs": ["Windsor", "Prahran"],
                         "property_types": ["House", "Townhouse"],
                         "min_land_size_sqm": 300})
    doc = yaml.safe_load(cfg.read_text())
    assert doc["filters"]["price_ceiling"] == 950000
    assert doc["filters"]["min_bedrooms"] == 4
    assert doc["filters"]["suburbs"] == ["Windsor", "Prahran"]
    assert doc["filters"]["property_types"] == ["House", "Townhouse"]
    assert doc["filters"]["min_land_size_sqm"] == 300
    # Untouched neighbours keep their values.
    assert doc["filters"]["suburbs_mode"] == "list"
    assert doc["fetch"]["fetcher"] == "scrapedo"
    assert doc["fetch"]["chrome"]["version_main"] is None


def test_null_is_written_as_null_not_an_empty_value(cfg):
    write_settings(cfg, {"stretch_ceiling": 1500000})
    write_settings(cfg, {"stretch_ceiling": None})
    assert "  stretch_ceiling: null" in cfg.read_text()


def test_read_reports_current_values(cfg):
    s = read_settings(cfg)
    assert s["values"]["suburbs"] == ["Windsor", "Richmond"]
    assert s["values"]["price_ceiling"] == 1200000
    assert s["values"]["stretch_ceiling"] is None
    assert s["values"]["exclude_streets"] == []
    assert s["options"]["property_types"] == PROPERTY_TYPES


# --- rejected input must not touch the file ---------------------------------

@pytest.mark.parametrize("bad", [
    {"price_ceiling": "not a number"},
    {"price_ceiling": -5},
    {"price_ceiling": 0},
    {"min_bedrooms": 99},
    {"min_land_size_sqm": -1},
    {"property_types": ["Castle"]},
    {"suburbs": []},
    {"suburbs": ""},
    {"price_ceiling": 1200000, "stretch_ceiling": 900000},
])
def test_invalid_values_are_rejected_and_change_nothing(cfg, bad):
    original = cfg.read_text()
    with pytest.raises(SettingsError):
        write_settings(cfg, bad)
    assert cfg.read_text() == original


def test_fields_outside_the_whitelist_are_refused(cfg):
    """The panel must not be able to reach selectors, rate limits or the
    fetch layer — a typo in the browser can't be allowed to break parsing."""
    original = cfg.read_text()
    for bad in ({"fetcher": "requests"}, {"state_script_regex": ".*"},
                {"min_delay_seconds": 0}, {"enabled": False}):
        with pytest.raises(SettingsError):
            write_settings(cfg, bad)
    assert cfg.read_text() == original


def test_missing_key_is_reported_rather_than_appended(cfg):
    """If someone deletes a key from config.yaml, silently re-adding it in
    the wrong place would be worse than saying so."""
    cfg.write_text(CONFIG.replace("  min_bedrooms: 2\n", ""))
    with pytest.raises(SettingsError, match="min_bedrooms"):
        write_settings(cfg, {"min_bedrooms": 3})


def test_partial_submissions_leave_other_fields_alone(cfg):
    write_settings(cfg, {"price_ceiling": 999000})
    values = read_settings(cfg)["values"]
    assert values["price_ceiling"] == 999000
    assert values["suburbs"] == ["Windsor", "Richmond"]
    assert values["property_types"] == ["House"]


# --- input shapes the browser actually sends --------------------------------

def test_comma_separated_strings_and_lists_both_work(cfg):
    write_settings(cfg, {"exclude_streets": " Bell St , Sydney Rd ,, "})
    assert read_settings(cfg)["values"]["exclude_streets"] == ["Bell St", "Sydney Rd"]
    write_settings(cfg, {"exclude_streets": ["High St", "High St"]})
    assert read_settings(cfg)["values"]["exclude_streets"] == ["High St"]


def test_money_accepts_the_formatting_a_person_types(cfg):
    write_settings(cfg, {"price_ceiling": "$1,450,000"})
    assert read_settings(cfg)["values"]["price_ceiling"] == 1450000


def test_empty_property_types_means_every_type(cfg):
    write_settings(cfg, {"property_types": []})
    assert read_settings(cfg)["values"]["property_types"] == []
    assert "  property_types: []" in cfg.read_text()


def test_every_whitelisted_field_targets_the_filters_section(cfg):
    """A field pointed at the wrong section would edit something the panel
    doesn't describe."""
    for name, field in FIELDS.items():
        assert field.path[0] == "filters", name
        assert len(field.path) == 2, name
