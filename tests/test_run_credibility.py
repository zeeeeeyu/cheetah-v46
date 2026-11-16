from quant.run_archive.credibility import RunCredibility, most_credible, tag_run


def test_tag_actual():
    assert tag_run(source="x", has_reconciliation=True) == RunCredibility.ACTUAL_CONFIRMED


def test_tag_plan():
    assert tag_run(source="plan_2026") == RunCredibility.PLAN_INFERRED


def test_tag_model():
    assert tag_run(source="model_run") == RunCredibility.MODEL_INFERRED


def test_tag_legacy():
    assert tag_run(source="legacy_dump") == RunCredibility.UNKNOWN_LEGACY_STATE
    assert tag_run(source="") == RunCredibility.UNKNOWN_LEGACY_STATE


def test_most_credible():
    recs = [("a", RunCredibility.MODEL_INFERRED), ("b", RunCredibility.ACTUAL_CONFIRMED)]
    assert most_credible(recs) == RunCredibility.ACTUAL_CONFIRMED
