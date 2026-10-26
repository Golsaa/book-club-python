from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Member(Base):
    __tablename__ = "members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    favorite_genre: Mapped[str] = mapped_column(String(40), nullable=False)
    google_subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="member")
    votes: Mapped[list["Vote"]] = relationship(back_populates="member")

    __table_args__ = (UniqueConstraint("email", name="uq_members_email"),)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    author: Mapped[str] = mapped_column(String(160), nullable=False)
    genre: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    member: Mapped[Member] = relationship(back_populates="recommendations")
    votes: Mapped[list["Vote"]] = relationship(back_populates="recommendation")


Index(
    "uq_recommendations_current_per_member",
    Recommendation.member_id,
    unique=True,
    sqlite_where=Recommendation.archived_at.is_(None),
    postgresql_where=Recommendation.archived_at.is_(None),
)


class Vote(Base):
    __tablename__ = "votes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), nullable=False)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id", ondelete="RESTRICT"), nullable=False)
    cast_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    member: Mapped[Member] = relationship(back_populates="votes")
    recommendation: Mapped[Recommendation] = relationship(back_populates="votes")


Index(
    "uq_votes_one_active_per_member",
    Vote.member_id,
    unique=True,
    sqlite_where=Vote.is_active.is_(True),
    postgresql_where=Vote.is_active.is_(True),
)


class EmailFailure(Base):
    __tablename__ = "email_failures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    error_summary: Mapped[str] = mapped_column(String(500), nullable=False)


class AppSettings(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    voting_first_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    voting_period_days: Mapped[int] = mapped_column(Integer, nullable=False)


class Genre(Base):
    __tablename__ = "genres"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)


class VotingClosure(Base):
    __tablename__ = "voting_closures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    closed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VotingRound(Base):
    __tablename__ = "voting_rounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    selected_recommendation_id: Mapped[int | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL")
    )
    selected_title: Mapped[str | None] = mapped_column(String(200))
    selected_author: Mapped[str | None] = mapped_column(String(160))
    selected_genre: Mapped[str | None] = mapped_column(String(40))
    selected_description: Mapped[str | None] = mapped_column(Text)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
