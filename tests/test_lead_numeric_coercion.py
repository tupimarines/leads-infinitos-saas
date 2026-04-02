"""Testes para normalização de números ao inserir campaign_leads (CSV BR / milhar com ponto)."""

from utils.lead_numeric_parse import coerce_lead_numeric_fields, parse_loose_float


def test_parse_loose_float_br_and_thousands():
    assert parse_loose_float("1.234,56") == 1234.56
    assert parse_loose_float("-253.999.866") == -253999866.0
    assert parse_loose_float("47.0") == 47.0
    assert parse_loose_float("4,4") == 4.4
    assert parse_loose_float(None) is None
    assert parse_loose_float("") is None


def test_coerce_lat_lon_out_of_range_becomes_none():
    lead = {
        "reviews_count": "47",
        "reviews_rating": "4.4",
        "latitude": "-253.999.866",
        "longitude": "-49.2733",
    }
    coerce_lead_numeric_fields(lead)
    assert lead["reviews_count"] == 47.0
    assert lead["reviews_rating"] == 4.4
    assert lead["latitude"] is None
    assert lead["longitude"] == -49.2733


def test_coerce_valid_coords():
    lead = {
        "latitude": "-25,4308",
        "longitude": "-49,2733",
    }
    coerce_lead_numeric_fields(lead)
    assert abs(lead["latitude"] + 25.4308) < 1e-6
    assert abs(lead["longitude"] + 49.2733) < 1e-6


if __name__ == "__main__":
    test_parse_loose_float_br_and_thousands()
    test_coerce_lat_lon_out_of_range_becomes_none()
    test_coerce_valid_coords()
    print("ok")
