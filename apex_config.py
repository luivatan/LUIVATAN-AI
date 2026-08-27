"""Centralized environment configuration for local and hosted Apex AI."""
from dataclasses import dataclass
import os
@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    model_dir: str = os.getenv("APEX_MODEL_DIR", "models")
    model_path: str = os.getenv("APEX_MODEL_PATH", "")
    environment: str = os.getenv("APEX_ENV", "development")
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:4173")
    secure_cookies: bool = os.getenv("APEX_ENV", "development") == "production"
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "100"))

def settings() -> Settings: return Settings()
