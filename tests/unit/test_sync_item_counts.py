from src.admin.services import sync_item_counts
from src.services.sync_progress import sync_item_count_from_progress


def test_admin_sync_item_count_helper_reexports_service_helper():
    assert sync_item_counts.sync_item_count_from_progress is sync_item_count_from_progress
