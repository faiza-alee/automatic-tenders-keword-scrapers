"""Central configuration, read from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- database ---
    database_url: str = "postgresql+psycopg2://tenders:tenders@localhost:5432/tenders"

    # --- API security (comma-separated lists; empty list => every request is rejected) ---
    api_keys: str = ""
    admin_api_keys: str = ""
    rate_limit_per_minute: int = 120

    # --- monitoring / scheduler ---
    scheduler_enabled: bool = True
    scrape_interval_minutes: int = 60
    run_on_start: bool = True
    run_timeout_minutes: int = 120  # a 'running' log older than this is treated as abandoned

    # --- scraper ---
    site_config_path: str = "config/site.yaml"
    request_delay_seconds: float = 2.0
    request_timeout_seconds: int = 30
    max_retries: int = 3
    max_pages: int = 20
    user_agent: str = "GBPPRA-TenderMonitor/1.0 (+contact: set-your-email@example.com)"
    respect_robots: bool = True
    download_documents: bool = True
    max_document_mb: int = 20
    max_document_attempts: int = 3
    ocr_enabled: bool = False

    # --- NLP ---
    keyword_method: str = "yake"  # yake | tfidf | keybert
    taxonomy_path: str = "config/taxonomy.yaml"
    max_nlp_attempts: int = 3
    max_keywords: int = 15

    log_level: str = "INFO"

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def admin_key_set(self) -> set[str]:
        return {k.strip() for k in self.admin_api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
