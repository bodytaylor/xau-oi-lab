# config.py
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


@dataclass
class Settings:
    discord_webhook_url: str
    supabase_url: str
    supabase_service_key: str
    tv_cdp_port: int = 9222

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            supabase_url=os.environ.get("SUPABASE_URL", ""),
            supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", ""),
            tv_cdp_port=int(os.environ.get("TV_CDP_PORT", "9222")),
        )


settings = Settings.from_env()
