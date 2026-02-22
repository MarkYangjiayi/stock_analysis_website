from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    环境配置管理
    所有的环境变量加载与校验，都将通过这个 Pydantic 模型完成。
    """
    
    # 数据库连接配置
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/stock_analysis"
    
    # EODHD API 配置
    EODHD_API_KEY: str = "demo"
    EODHD_BASE_URL: str = "https://eodhd.com/api"
    GEMINI_API_KEY: str = "demo"

    # 指定配置加载来源：.env 文件
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 中存在但在 Settings 模型未定义的额外变量
    )


# 实例化产生全局唯一配置对象供其他模块导入使用
settings = Settings()
