from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/airtel_mock"

    client_id: str
    client_secret: str
    token_ttl_seconds: int = 180

    bank_base_url: str = ""
    bank_opt_in_path: str = ""
    bank_check_eligibility_path: str = ""
    bank_apply_loan_path: str = ""
    bank_timeout_seconds: float = 30.0

    @property
    def bank_opt_in_url(self) -> str | None:
        return self._bank_url(self.bank_opt_in_path)

    @property
    def bank_check_eligibility_url(self) -> str | None:
        return self._bank_url(self.bank_check_eligibility_path)

    @property
    def bank_apply_loan_url(self) -> str | None:
        return self._bank_url(self.bank_apply_loan_path)

    def _bank_url(self, path: str) -> str | None:
        """URL complète d'un endpoint de la banque, ou None tant qu'il n'est pas configuré."""
        if not self.bank_base_url or not path:
            return None
        return self.bank_base_url.rstrip("/") + "/" + path.lstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
