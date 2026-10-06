from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BankMockSettings(BaseSettings):
    """Réglages du bank-mock, lus dans le même .env que le mock Airtel (préfixe BANK_MOCK_)."""

    model_config = SettingsConfigDict(env_prefix="BANK_MOCK_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Fichier des prêts accordés, relatif au dossier de lancement
    loans_file: str = "bank_mock_loans.json"

    # Durée de la réponse lente (scénario 3), à garder au-dessus de BANK_TIMEOUT_SECONDS
    slow_seconds: float = 35.0

    min_amount: int = 5000
    max_amount: int = 100000
    eligible_amount: int = 50000
    interest_rate: int = 2  # en %, appliqué aux frais
    tenure_days: int = 30


@lru_cache
def get_settings() -> BankMockSettings:
    return BankMockSettings()
