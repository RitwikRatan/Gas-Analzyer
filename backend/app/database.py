import os
import sys
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

# Search for .env in current dir and backend dir
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Resolve SQLite fallback path
default_sqlite_path = Path(__file__).resolve().parent.parent / "gas_analyzer.db"
fallback_sqlite_url = f"sqlite:///{default_sqlite_path}"

engine = None

if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    try:
        # Test whether the PostgreSQL instance is actively reachable
        test_engine = create_engine(
            DATABASE_URL,
            pool_size=10,
            max_overflow=20,
            pool_timeout=5,
            pool_pre_ping=True
        )
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine = test_engine
        print(f"[Database] Successfully connected to PostgreSQL instance.")
    except Exception as e:
        print(f"[Database Warning] PostgreSQL connection failed ({e}). Falling back to local SQLite at {default_sqlite_path}.")
        engine = create_engine(fallback_sqlite_url, connect_args={"check_same_thread": False})
elif DATABASE_URL and DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(fallback_sqlite_url, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependency for FastAPI to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
