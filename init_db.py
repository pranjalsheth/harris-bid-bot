from src.config import get_settings
from src.db import get_engine, init_db

if __name__ == "__main__":
    settings = get_settings()
    engine = get_engine(settings)
    init_db(engine)
    print("Database initialized.")
