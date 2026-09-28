from railcorridor.stations import (
    Candidate,
    display_name,
    is_hub_name,
    merge_candidates,
    names_compatible,
    normalise,
)


def cand(feed, name, lat, lon):
    return Candidate(feed, f"{feed}:{name}", name, lat, lon, 1)


def test_czech_abbreviation_expands():
    assert normalise("Praha hl.n.") == normalise("Praha hlavní nádraží")


def test_same_station_from_two_feeds_merges():
    cs = [
        cand("de", "Praha hl.n.", 50.08306, 14.436039),
        cand("cz", "Praha hlavní nádraží", 50.08309, 14.435978),
    ]
    groups = merge_candidates(cs)
    assert groups[0] == groups[1]


def test_nearby_but_different_stations_stay_apart():
    cs = [
        cand("de", "Hamburg Hbf", 53.5530, 10.0069),
        cand("de", "Hamburg Dammtor", 53.5606, 9.9897),
        cand("de", "Hamburg Hbf", 53.5530, 10.0069 + 0.02),  # ~1.3 km east
    ]
    groups = merge_candidates(cs)
    assert len(set(groups)) == 3


def test_platform_style_names_merge_with_the_station():
    assert names_compatible("S+U Berlin Hauptbahnhof", "Berlin Hbf")
    assert names_compatible("Hamburg, HBF/Kirchenallee", "Hamburg Hbf")
    assert not names_compatible("Hamburg Hbf", "Altona")


def test_display_name_prefers_plain_and_diacritics():
    assert display_name([("Hamburg, HBF/Kirchenallee", 9), ("Hamburg Hbf", 1)]) == (
        "Hamburg Hbf"
    )
    assert display_name([("Decin hl.n.", 9), ("Děčín hl.n.", 1)]) == "Děčín hl.n."


def test_hub_names():
    assert is_hub_name("S+U Berlin Hauptbahnhof")
    assert is_hub_name("Praha, hlavni nadrazi")
    assert not is_hub_name("Berlin Südkreuz")
