import pytest

from app.config import Settings

SERVICES = {
    "subscription": ["opt_in"],
    "origination": ["check_eligibility", "apply_loan", "apply_loan_status", "confirm_loan", "cancel_loan"],
    "repayment": ["lookup_details", "repay_loan", "repay_loan_status", "topup_notification"],
    "collection": ["mini_statement", "balance_enquiry"],
}
ENDPOINTS = [(service, name) for service, names in SERVICES.items() for name in names]


def settings(**overrides) -> Settings:
    # Tout est explicite : ni les variables d'environnement ni le .env local n'interviennent
    paths = {f"bank_{name}_path": f"/{name}" for _, name in ENDPOINTS}
    bases = {"bank_base_url": "", **{f"bank_{service}_base_url": "" for service in SERVICES}}
    return Settings(client_id="a", client_secret="b", **{**paths, **bases, **overrides})


@pytest.mark.parametrize("service, name", ENDPOINTS)
def test_each_endpoint_uses_its_microservice(service, name):
    ports = {"subscription": 8089, "origination": 8082, "repayment": 8083, "collection": 8084}
    s = settings(bank_base_url="http://fallback:9000",
                 **{f"bank_{svc}_base_url": f"http://bank:{port}/" for svc, port in ports.items()})
    assert getattr(s, f"bank_{name}_url") == f"http://bank:{ports[service]}/{name}"


@pytest.mark.parametrize("service, name", ENDPOINTS)
def test_empty_service_base_falls_back_to_bank_base_url(service, name):
    assert getattr(settings(bank_base_url="http://localhost:9000"), f"bank_{name}_url") == f"http://localhost:9000/{name}"


def test_not_configured_without_any_base_or_path():
    assert settings(bank_base_url="", bank_origination_base_url="").bank_apply_loan_url is None
    assert settings(bank_origination_base_url="http://bank:8082", bank_apply_loan_path="").bank_apply_loan_url is None
