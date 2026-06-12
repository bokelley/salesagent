from src.services.sync_progress import build_sync_progress, sync_item_count_from_progress


def test_sync_item_count_preserves_explicit_zero():
    assert sync_item_count_from_progress({"item_count": 0}) == 0


def test_sync_item_count_falls_back_to_known_count_fields():
    assert sync_item_count_from_progress({"counts": {"products_updated": 7}}) == 7
    assert sync_item_count_from_progress({"counts": {"signals_updated": 3}}) == 3


def test_sync_item_count_falls_back_to_count_sum_for_legacy_progress():
    assert sync_item_count_from_progress({"counts": {"ad_units": 2, "placements": 5}}) == 7


def test_sync_item_count_optionally_accepts_items_processed():
    assert sync_item_count_from_progress({"items_processed": 0}, include_items_processed=True) == 0
    assert sync_item_count_from_progress({"items_processed": 4}, include_items_processed=True) == 4
    assert sync_item_count_from_progress({"items_processed": 4}) is None
    assert sync_item_count_from_progress({"counts": {"items_processed": 4}}) is None
    assert sync_item_count_from_progress({"counts": {"items_processed": 4}}, include_items_processed=True) == 4


def test_build_sync_progress_emits_top_level_item_count():
    progress = build_sync_progress(
        counts={"signals_updated": 0},
        errors={},
        updated_signal_ids=[],
    )

    assert progress["item_count"] == 0
    assert progress["counts"] == {"signals_updated": 0}
    assert progress["errors"] == {}
    assert progress["updated_signal_ids"] == []


def test_build_sync_progress_prefers_explicit_item_count():
    progress = build_sync_progress(counts={"ad_units": 2, "placements": 5}, errors={}, item_count=2)

    assert progress["item_count"] == 2
