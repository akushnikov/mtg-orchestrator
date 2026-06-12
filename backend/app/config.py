from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    docker_host: str = "tcp://docker-socket-proxy:2375"
    panel_domain: str
    moscow_ip: str
    bot_token: str = ""
    owner_user_id: int = 0
    webhook_secret: str = ""
    # Host Telegram should hit for webhook delivery. Defaults to panel_domain,
    # but the panel domain points at the Moscow relay (good for the RU owner
    # opening the Mini App, bad for Telegram's foreign servers — they time out
    # through the cascade). Set this to a hostname that resolves DIRECTLY to the
    # Frankfurt host so webhook delivery bypasses Moscow.
    webhook_domain: str = ""
    dev_mock_init_data: bool = False
    mtg_default_domain: str = ""
    mtg_default_secret: str = ""
    db_path: str = "/data/db/registry.db"
    mtg_configs_dir: str = "/data/mtg-configs"
    nginx_config_dir: str = "/data/nginx"
    mtg_port_range_start: int = 20000
    mtg_port_range_end: int = 29999


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = Settings()
