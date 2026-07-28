"""Map location banners must not be logged as gamertags — but real players who
happen to share a word with a POI must still count.

Stan spotted this: "Algae ponds isn't a name of a runner, it's an area in dire
marsh." On 2026-07-27 BOTH bad names were Dire Marsh POIs, and ALGAE PONDS was
promoted to the WITNESS Report's PRIME TARGET.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encounters  # noqa: E402


# The two strings actually logged as "you downed" on 2026-07-27.
def test_real_false_names_from_the_session_are_rejected():
    assert encounters.location_hit("ALGAE PONDS") == "Dire Marsh · Algae Ponds"
    # OCR carried a scrap along: the string was "QUARANTINE MM", not "QUARANTINE".
    assert encounters.location_hit("QUARANTINE MM") == "Dire Marsh · Quarantine"
    assert not encounters._is_player("ALGAE PONDS")
    assert not encounters._is_player("QUARANTINE MM")


def test_every_known_poi_is_recognised():
    for map_name, pois in encounters.MAP_LOCATIONS.items():
        for poi in pois:
            assert encounters.location_hit(poi), f"{map_name}: {poi}"


def test_real_gamertags_from_the_session_still_count():
    """Every genuine victim name from 2026-07-27 must survive the filter."""
    for name in ["PiNDLESKIN", "MEGAMAN718", "CLOGGZ", "MIGHTY MO THUG",
                 "VOXODII", "SAMURY", "GUARDIAN8459", "MZZ", "DANBOY324",
                 "GOOSE", "MRVIZNASTY", "SupremePlays"]:
        assert encounters.location_hit(name) is None, name
        assert encounters._is_player(name), name


def test_short_poi_words_are_matched_exactly_not_fuzzily():
    """INDEX / CARGO / CANAL are real POIs but plausible gamertags. Only an exact
    match may suppress them — a near miss must stay a player."""
    assert encounters.location_hit("INDEX")          # the POI itself
    for tag in ["INDEXER", "CARGO99", "xXCanalXx", "CONTROLLER", "ANOMALYX",
                "STATIONS", "RAVINES"]:
        assert encounters.location_hit(tag) is None, tag
        assert encounters._is_player(tag), tag


def test_long_poi_names_tolerate_ocr_slips():
    # A one-character OCR slip on a long, distinctive name still reads as the POI.
    assert encounters.location_hit("ALGAE PONOS")
    assert encounters.location_hit("BIO-RESEARGH")
    # But something genuinely different is left alone.
    assert encounters.location_hit("ALGAEBROTHER47") is None


def test_ability_text_still_filtered():
    """The earlier regression: the Destroyer ult read as a victim name."""
    assert not encounters._is_player("SEARCH AND DESTROY")


def test_location_filter_can_be_bypassed():
    """_is_player(skip_locations=False) leaves POI handling to the caller."""
    assert encounters._is_player("ALGAE PONDS", skip_locations=False)


def test_config_name_ignore_still_applies():
    assert not encounters._is_player("SOMEONE", ignore={"SOMEONE"})
