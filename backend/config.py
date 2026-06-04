from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Waypoint"
    app_env: str = "development"
    app_debug: bool = True

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")
    youtube_data_api_key: str = Field(default="", alias="YOUTUBE_DATA_API_KEY")
    github_api_token: str = Field(default="", alias="GITHUB_API_TOKEN")
    llm_timeout_seconds: int = Field(default=180, alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_retry_base_delay_seconds: float = Field(default=0.5, alias="LLM_RETRY_BASE_DELAY_SECONDS")

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/waypoint",
        alias="DATABASE_URL",
    )
    direct_url: str | None = Field(default=None, alias="DIRECT_URL")
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout_seconds: int = Field(default=30, alias="DB_POOL_TIMEOUT_SECONDS")
    db_pool_recycle_seconds: int = Field(default=1800, alias="DB_POOL_RECYCLE_SECONDS")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_username: str | None = Field(default=None, alias="REDIS_USERNAME")
    redis_key_prefix: str = Field(default="", alias="REDIS_KEY_PREFIX")
    cors_allow_origins: str = Field(
    default="http://localhost:3000,http://127.0.0.1:3000,https://waypointapp.vercel.app",
    )

    generation_rate_limit_enabled: bool = Field(default=True, alias="GEN_RATE_LIMIT_ENABLED")
    generation_burst_limit_count: int = Field(default=1, alias="GEN_BURST_LIMIT_COUNT")
    generation_burst_window_seconds: int = Field(default=10, alias="GEN_BURST_WINDOW_SECONDS")
    generation_hourly_limit_count: int = Field(default=6, alias="GEN_HOURLY_LIMIT_COUNT")
    generation_hourly_window_seconds: int = Field(default=3600, alias="GEN_HOURLY_WINDOW_SECONDS")
    security_headers_enabled: bool = Field(default=True, alias="SECURITY_HEADERS_ENABLED")
    security_hsts_enabled: bool = Field(default=False, alias="SECURITY_HSTS_ENABLED")


    dependency_cache_ttl_seconds: int = 60 * 60 * 24 * 30
    curriculum_cache_ttl_seconds: int = 60 * 60 * 24 * 7
    video_cache_ttl_seconds: int = 60 * 60 * 24 * 7

    max_video_candidates: int = 80
    max_channel_repeat: int = 2
    youtube_request_timeout_seconds: int = Field(default=8, alias="YOUTUBE_REQUEST_TIMEOUT_SECONDS")
    youtube_queries_per_concept: int = Field(default=2, alias="YOUTUBE_QUERIES_PER_CONCEPT")
    youtube_concept_limit: int = Field(default=6, alias="YOUTUBE_CONCEPT_LIMIT")
    youtube_search_max_results: int = Field(default=20, alias="YOUTUBE_SEARCH_MAX_RESULTS")

    # Video length preference multipliers
    long_video_multiplier: float = Field(default=1.5, alias="LONG_VIDEO_MULTIPLIER")
    mid_video_multiplier: float = Field(default=1.2, alias="MID_VIDEO_MULTIPLIER")
    short_video_multiplier: float = Field(default=0.6, alias="SHORT_VIDEO_MULTIPLIER")
    short_video_threshold_seconds: int = Field(default=900, alias="SHORT_VIDEO_THRESHOLD_SECONDS")
    mid_video_threshold_seconds: int = Field(default=1800, alias="MID_VIDEO_THRESHOLD_SECONDS")
    long_video_threshold_seconds: int = Field(default=3600, alias="LONG_VIDEO_THRESHOLD_SECONDS")

    # Embeddings settings (optional)
    embeddings_provider: str = Field(default="openai", alias="EMBEDDINGS_PROVIDER")
    embeddings_api_key: str = Field(default="", alias="EMBEDDINGS_API_KEY")
    embeddings_model: str = Field(default="text-embedding-3-small", alias="EMBEDDINGS_MODEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()


    

