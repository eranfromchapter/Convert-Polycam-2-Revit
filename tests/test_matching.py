from src.matching import (best_match, extract_unit, normalize_address, score,
                          write_review_csv, read_approved_table, build_matching_table)


def test_normalize_strips_unit_and_zip():
    assert normalize_address("1500 Bay Rd #1462, Miami Beach, FL 33139") == \
        normalize_address("1500 Bay Road, Miami Beach")


def test_normalize_expands_abbreviations():
    assert normalize_address("100 N Main St") == "100 north main street"


def test_unit_extraction():
    assert extract_unit("1500 Bay Rd #1462") == "1462"
    assert extract_unit("22 Elm St Apt 4B") == "4B"
    assert extract_unit("22 Elm St") is None


def test_score_reference_case():
    # The real matching pair from the reference project.
    assert score("1500 Bay Rd #1462", "1500 Bay Rd, Miami Beach, FL") >= 0.9


def test_score_rejects_different_streets():
    assert score("1500 Bay Rd", "700 Collins Ave, Miami Beach") < 0.7


def test_best_match_prefers_dealid_over_id():
    deal = {"id": "project-record-id", "dealId": "the-real-deal-id",
            "address": "1500 Bay Rd #1462"}
    captures = [{"id": "cap1", "name": "1500 Bay Rd, Miami Beach, FL"},
                {"id": "cap2", "name": "9 Ocean Dr"}]
    m = best_match(deal, captures)
    assert m.deal_id == "the-real-deal-id"
    assert m.capture_id == "cap1"
    assert m.confidence >= 0.9


def test_review_csv_roundtrip(tmp_path):
    deals = [{"dealId": "d1", "address": "1500 Bay Rd #1462"},
             {"dealId": "d2", "address": "999 Nowhere Ln"}]
    captures = [{"id": "c1", "name": "1500 Bay Rd, Miami Beach, FL"}]
    matches = build_matching_table(deals, captures)
    csv_path = tmp_path / "review.csv"
    n_review = write_review_csv(matches, csv_path, 0.9)
    assert n_review == 1  # the Nowhere Ln row scores low → needs review
    approved = read_approved_table(csv_path)  # only pre-approved 'yes' rows
    assert [m.deal_id for m in approved] == ["d1"]
