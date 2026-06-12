from src.admin.services.sync_item_counts import sync_item_count_from_progress


def test_sync_item_count_preserves_explicit_zero():
    assert sync_item_count_from_progress({"item_count": 0}) == 0


def test_sync_item_count_falls_back_to_known_count_fields():
    assert sync_item_count_from_progress({"counts": {"products_updated": 7}}) == 7
    assert sync_item_count_from_progress({"counts": {"signals_updated": 3}}) == 3


def test_sync_item_count_optionally_accepts_items_processed():
    assert sync_item_count_from_progress({"items_processed": 0}, include_items_processed=True) == 0
    assert sync_item_count_from_progress({"items_processed": 4}, include_items_processed=True) == 4
    assert sync_item_count_from_progress({"items_processed": 4}) is None
