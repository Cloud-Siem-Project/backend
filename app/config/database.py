from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config.app_config import settings

# Engine (PostgreSQL bağlantısı)
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # bağlantı koparsa otomatik yeniler
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# FastAPI dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()