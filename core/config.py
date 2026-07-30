from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    环境配置管理
    所有的环境变量加载与校验，都将通过这个 Pydantic 模型完成。
    """
    
    # 数据库连接配置
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/quantify_local.db"
    ENVIRONMENT: str = "development"
    APP_VERSION: str = "2.0.0"
    
    # EODHD API 配置
    EODHD_API_KEY: str = "demo"
    EODHD_BASE_URL: str = "https://eodhd.com/api"
    GEMINI_API_KEY: str = "demo"
    
    # Notifications
    FEISHU_WEBHOOK_URL: str = ""

    # API and browser security. When omitted, admin operations are disabled.
    ADMIN_API_KEY: str = ""
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    EXPENSIVE_REQUESTS_PER_MINUTE: int = 3
    TRUSTED_PROXY_IPS: str = "127.0.0.1,::1,172.16.0.0/12"

    # On-demand anomaly scans are persisted and run outside the HTTP request.
    # Keep the fan-out and individual provider calls bounded so one scan cannot
    # monopolize the web process or create unbounded third-party spend.
    ANOMALY_MOVE_THRESHOLD_PCT: float = 4.0
    ANOMALY_RESULT_LIMIT: int = 5
    ANOMALY_ATTRIBUTION_CONCURRENCY: int = 3
    ANOMALY_ATTRIBUTION_TIMEOUT_SECONDS: float = 30.0
    ANOMALY_SCAN_TIMEOUT_SECONDS: float = 90.0
    ANOMALY_NEWS_LOOKBACK_HOURS: int = 24

    # Background workers are intentionally disabled in the web process. Run
    # `python worker.py` as a separate service instead.
    RUN_BACKGROUND_TASKS: bool = False
    ENABLE_WS_MONITOR: bool = False
    WS_SYMBOLS: str = "AAPL,NVDA,TSLA,ASTS"
    WS_WINDOW_SECONDS: int = 300
    WS_ALERT_THRESHOLD: float = 0.015
    WS_COOLDOWN_SECONDS: int = 900

    # Data pipeline defaults
    DATA_DIR: str = "./data"
    RAW_DATA_DIR: str = "./data/raw"
    BACKUP_DIR: str = "./data/backups"
    COLD_START_HISTORY_DAYS: int = 252
    HISTORY_BACKFILL_CONCURRENCY: int = 2
    PIPELINE_MIN_PRICE_COVERAGE: float = 0.95
    PIPELINE_MIN_FUNDAMENTAL_COVERAGE: float = 0.80
    PIPELINE_MIN_UNIVERSE_COVERAGE: float = 0.90
    PIPELINE_MIN_PRICE_FACTOR_COVERAGE: float = 0.80
    PIPELINE_MIN_UNIVERSE_SIZE: int = 100
    PIPELINE_MIN_SP500_SIZE: int = 400
    PIPELINE_MIN_RUSSELL2000_SIZE: int = 1500
    PROFILE_MAX_STALENESS_DAYS: int = 7

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def trusted_proxy_ips(self) -> List[str]:
        return [value.strip() for value in self.TRUSTED_PROXY_IPS.split(",") if value.strip()]

    # 指定配置加载来源：.env 文件
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 中存在但在 Settings 模型未定义的额外变量
    )


# 实例化产生全局唯一配置对象供其他模块导入使用
settings = Settings()

# Create only application-owned runtime directories. Database-specific parent
# creation remains in database.py because remote databases do not use them.
for runtime_dir in (settings.DATA_DIR, settings.RAW_DATA_DIR, settings.BACKUP_DIR):
    Path(runtime_dir).mkdir(parents=True, exist_ok=True)
