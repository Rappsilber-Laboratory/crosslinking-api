from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db_config_parser import get_conn_str

conn_str = get_conn_str()

engine = create_engine(
    conn_str,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Create SessionLocal class from sessionmaker factory
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
