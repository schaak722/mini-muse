import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")

    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in your hosting environment variables.")

    # psycopg v3 SQLAlchemy URL prefix
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JSON_SORT_KEYS = False
