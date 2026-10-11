import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_CLAIM_EMAIL: str = ""
    # Read from the environment only — never committed. A demo is a public page,
    # so the key stays server-side and the browser only ever talks to our own
    # /api/demo/{job_id}/ai route.
    ANTHROPIC_API_KEY: str = ""
    DEMO_AI_MODEL: str = "claude-sonnet-5-5"
    FRONTEND_URL: str = ""
    CORS_ORIGINS: str = ""  # Comma-separated list of allowed origins

    model_config = {"env_file": ".env", "extra": "ignore"}

    def get_cors_origins(self) -> list[str]:
        """Build the list of allowed CORS origins from config."""
        origins = [
            "http://localhost:3000",
            "http://localhost:3001",
            # Public production domain of the frontend (uav Vercel project)
            "https://uav-theta.vercel.app",
        ]
        if self.FRONTEND_URL:
            origins.append(self.FRONTEND_URL.rstrip("/"))
        if self.CORS_ORIGINS:
            for origin in self.CORS_ORIGINS.split(","):
                stripped = origin.strip().rstrip("/")
                if stripped and stripped not in origins:
                    origins.append(stripped)
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
