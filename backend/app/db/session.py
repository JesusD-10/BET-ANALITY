from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings


def _sqlalchemy_url(value: str) -> str:
    """Accept Render's legacy postgres scheme alongside SQLAlchemy URLs."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg2://" + value[len("postgres://") :]
    return value


database_url = _sqlalchemy_url(settings.database_url)
connect_args = {}
if database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency provider to yield a database session and close it after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
