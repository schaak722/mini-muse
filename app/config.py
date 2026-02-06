import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", h3G4g0mJxQ9n7pV1zR6kL2sT8wY5uE0aC3dF9hJ4)

    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Add it in your hosting environment variables.")

    # psycopg v3 SQLAlchemy URL prefix
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Add connection pooling to fix SSL drops
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    JSON_SORT_KEYS = False
