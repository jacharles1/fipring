import os

OURA_CLIENT_ID = os.getenv("OURA_CLIENT_ID", "")
OURA_CLIENT_SECRET = os.getenv("OURA_CLIENT_SECRET", "")
OURA_REDIRECT_URI = os.getenv("OURA_REDIRECT_URI", "http://localhost:8000/auth/callback")
OURA_SCOPES = "email personal daily heartrate workout tag session spo2 ring_configuration stress heart_health"

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/oura.db")
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "365"))
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
