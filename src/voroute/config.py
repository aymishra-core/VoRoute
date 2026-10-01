"""Settings loaded from the environment. Keys are placeholders until telephony and voice are wired."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_from_number: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    openai_api_key: str = ""
    public_base_url: str = ""


settings = Settings()
