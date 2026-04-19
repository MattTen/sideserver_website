"""ORM models."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    last_login: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class App(Base):
    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bundle_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    developer_name: Mapped[str] = mapped_column(String(255), default="Self", nullable=False)
    subtitle: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tint_color: Mapped[str] = mapped_column(String(8), default="833AB4", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="other", nullable=False)
    icon_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    screenshot_urls: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    featured: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    versions: Mapped[list["Version"]] = relationship(
        back_populates="app", cascade="all, delete-orphan", order_by="Version.uploaded_at.desc()"
    )


class Version(Base):
    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id", ondelete="CASCADE"), nullable=False)
    ipa_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    build_version: Mapped[str] = mapped_column(String(64), default="1", nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    min_os_version: Mapped[str] = mapped_column(String(32), default="14.0", nullable=False)
    changelog: Mapped[str] = mapped_column(Text, default="", nullable=False)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    app: Mapped[App] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("app_id", "version", "build_version", name="uix_app_version_build"),
        Index("ix_versions_uploaded_at", "uploaded_at"),
    )
