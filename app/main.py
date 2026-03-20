from fastapi import FastAPI
from app.config.app_config import settings
from sqlalchemy import text
from app.config.database import engine

app = FastAPI(
    title=settings.app_name,
    debug=settings.app_debug,
)

@app.get("/")
def root():
    return {
        "message": "CloudSIEM API is running"
    }

@app.get("/health")
def health_check():
    return {
        "success": True,
        "message": "CloudSIEM backend is running",
        "environment": settings.app_env,
    }

@app.get("/db-check")
def db_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        value = result.scalar()

    return {"database": "PostgreSQL is connected", "result": value}