"""Database schema.

Everything the Oura cloud returns is kept verbatim as JSON in `oura_documents`, so no
field is ever lost; the dashboard pulls what it needs out of the JSON. Heart rate is
high-volume time series, so it gets its own narrow table. `ble_packets` holds raw
captures from the ring itself (see ble/), which is where the reverse-engineering
work lands.
"""
import os
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Integer, String, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


class OuraDocument(Base):
    __tablename__ = "oura_documents"

    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    day: Mapped[date | None] = mapped_column(Date, index=True)
    data: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeartRate(Base):
    __tablename__ = "heartrate"

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    bpm: Mapped[int] = mapped_column(Integer)


class SyncState(Base):
    __tablename__ = "sync_state"

    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    synced_through: Mapped[date] = mapped_column(Date)


class OAuthToken(Base):
    __tablename__ = "oauth_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BlePacket(Base):
    __tablename__ = "ble_packets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    session: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32))  # "bleak" | "btsnoop"
    direction: Mapped[str] = mapped_column(String(8))  # "rx" (ring->host) | "tx"
    handle: Mapped[str] = mapped_column(String(64))  # characteristic UUID or ATT handle
    payload_hex: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


def make_engine(url: str = config.DATABASE_URL):
    if url.startswith("sqlite:///./"):
        os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")), exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db(eng=engine):
    Base.metadata.create_all(eng)
