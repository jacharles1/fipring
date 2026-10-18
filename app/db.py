"""Database schema.

`ring_events` is the lossless store: every history event the ring hands over, with
its raw body. `decoded` is the best-effort structured view and `ts` the wall-clock
time derived from the ring's time-sync anchors (see ring/timeline.py); both can be
recomputed from the raw columns at any time.
"""
import os
from datetime import datetime

from sqlalchemy import (JSON, BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    firmware: Mapped[str | None] = mapped_column(String(32))
    mac: Mapped[str | None] = mapped_column(String(32))
    battery: Mapped[int | None] = mapped_column(Integer)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)  # next ring timestamp to fetch
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RingEvent(Base):
    __tablename__ = "ring_events"
    __table_args__ = (
        UniqueConstraint("serial", "tag", "ring_ts", "body_hex", name="uq_ring_event"),
        Index("ix_ring_events_name_ts", "name", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(64))
    tag: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64))
    ring_ts: Mapped[int] = mapped_column(BigInteger)  # deciseconds on the ring's own counter
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body_hex: Mapped[str] = mapped_column(Text)
    decoded: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def make_engine(url: str = config.DATABASE_URL):
    if url.startswith("sqlite:///./"):
        os.makedirs(os.path.dirname(url.removeprefix("sqlite:///")), exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db(eng=engine):
    Base.metadata.create_all(eng)
