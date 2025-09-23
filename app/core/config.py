# config.py
# Centralized configuration management for the CogniSim AI application.

import os
# --- FIX: Import BaseSettings and SettingsConfigDict from 'pydantic_settings' ---
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl, SecretStr
from typing import List, Optional

class Settings(BaseSettings):
    """
    Defines and validates all application settings, loading them from environment variables.
    """
    # --- FIX: Use the modern 'model_config' for Pydantic v2 ---
    # This replaces the legacy 'class Config' and is more reliable.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Ignore extra fields in .env file
    )

    # --- Application Metadata ---
    APP_NAME: str = "CogniSim AI - Backend API"
    APP_VERSION: str = "1.3.0"

    # --- Supabase Configuration ---
    # These are critical and will raise an error if not set.
    # Using SecretStr hides the value in logs and tracebacks for better security.
    SUPABASE_URL: Optional[AnyHttpUrl] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[SecretStr] = None
    SUPABASE_ANON_KEY: Optional[SecretStr] = None

    # --- OAuth Configuration ---
    GITHUB_LOGIN: Optional[AnyHttpUrl] = None
    GOOGLE_LOGIN: Optional[AnyHttpUrl] = None

    # --- CORS Origins ---
    # A list of allowed origins for Cross-Origin Resource Sharing (CORS).
    # It's a string of comma-separated URLs.
    CORS_ORIGINS: List[AnyHttpUrl] = []

    # --- Encryption Configuration ---
    # Encryption settings for secure credential storage
    ENCRYPTION_SECRET_KEY: Optional[SecretStr] = None
    ENCRYPTION_SALT: Optional[str] = None

    # --- Development Mode ---
    # For development, we can use a simple encoding instead of encryption
    DEV_MODE: bool = True

    # --- Team Quotas ---
    # Daily cap on agent runs per team
    TEAM_DAILY_RUN_LIMIT: int = 100
    # Optional token budget over last 30 days (sum of input+output tokens)
    TEAM_30D_TOKEN_LIMIT: Optional[int] = None


# Create a single, importable instance of the settings
settings = Settings()

# Validate that required settings are provided
if settings.SUPABASE_URL is None or settings.SUPABASE_SERVICE_ROLE_KEY is None:
    raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in environment variables")
