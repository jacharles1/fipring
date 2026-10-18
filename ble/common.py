import json
import os
from datetime import datetime, timezone
from pathlib import Path

CAPTURE_DIR = Path(os.getenv("CAPTURE_DIR", "captures"))


class PacketSink:
    """Writes every captured packet to a local JSONL file and, if DATABASE_URL is set,
    to the ble_packets table so it shows up next to the cloud data."""

    def __init__(self, session: str, source: str):
        self.session, self.source = session, source
        CAPTURE_DIR.mkdir(exist_ok=True)
        self.file = open(CAPTURE_DIR / f"{session}.jsonl", "a")
        self.db = None
        if os.getenv("DATABASE_URL"):
            from app.db import SessionLocal, init_db
            init_db()
            self.db = SessionLocal()

    def write(self, direction: str, handle: str, payload: bytes, note: str | None = None,
              captured_at: datetime | None = None):
        captured_at = captured_at or datetime.now(timezone.utc)
        rec = {"captured_at": captured_at.isoformat(), "session": self.session, "source": self.source,
               "direction": direction, "handle": handle, "payload_hex": payload.hex(), "note": note}
        self.file.write(json.dumps(rec) + "\n")
        self.file.flush()
        if self.db is not None:
            from app.db import BlePacket
            self.db.add(BlePacket(**{**rec, "captured_at": captured_at}))
            self.db.commit()

    def close(self):
        self.file.close()
        if self.db is not None:
            self.db.close()
