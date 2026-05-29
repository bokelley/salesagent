from src.core.helpers.account_provisioning import _is_gam_company_name_collision


def test_gam_company_name_collision_detects_not_unique_fault():
    exc = Exception("[UniqueError.NOT_UNIQUE @ [0].name; trigger:'Sandbox']")

    assert _is_gam_company_name_collision(exc)
