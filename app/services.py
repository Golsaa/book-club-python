from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DEFAULT_GENRES, settings
from .models import AppSettings, EmailFailure, Genre, Recommendation, Vote, VotingClosure


def get_app_settings(db: Session) -> AppSettings:
    config = db.get(AppSettings, 1)
    if config is None:
        config = AppSettings(
            id=1,
            voting_first_start=settings.voting_first_start,
            voting_period_days=settings.voting_period_days,
        )
        db.add(config)
        db.flush()
    return config


def get_genres(db: Session) -> list[str]:
    genres = db.scalars(select(Genre).order_by(Genre.position)).all()
    if not genres:
        genres = [Genre(name=name, position=index) for index, name in enumerate(DEFAULT_GENRES)]
        db.add_all(genres)
        db.flush()
    return [genre.name for genre in genres]


def scheduled_voting_window(
    now: datetime | None = None, db: Session | None = None
) -> tuple[datetime, datetime] | None:
    """Return the schedule-defined UTC window, regardless of manual closure."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    if db is None:
        first_start = settings.voting_first_start
        period_days = settings.voting_period_days
    else:
        config = get_app_settings(db)
        first_start = config.voting_first_start
        if first_start.tzinfo is None:
            first_start = first_start.replace(tzinfo=timezone.utc)
        period_days = config.voting_period_days
    length = timedelta(days=period_days)
    if now < first_start:
        return None
    periods_elapsed = (now - first_start) // length
    start = first_start + periods_elapsed * length
    return start, start + length


def manual_voting_closed_until(db: Session, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    closure = db.get(VotingClosure, 1)
    if closure is None:
        return None
    closed_until = closure.closed_until
    if closed_until.tzinfo is None:
        closed_until = closed_until.replace(tzinfo=timezone.utc)
    return closed_until if now.astimezone(timezone.utc) < closed_until else None


def voting_window(now: datetime | None = None, db: Session | None = None) -> tuple[datetime, datetime] | None:
    """Return the current open voting window, or None before opening/while manually closed."""
    window = scheduled_voting_window(now=now, db=db)
    if window is None or db is None:
        return window
    if manual_voting_closed_until(db, now):
        return None
    return window


def list_recommendations_with_votes(db: Session) -> list[dict]:
    rows = db.execute(
        select(Recommendation, func.count(Vote.id).label("vote_count"))
        .outerjoin(Vote, (Vote.recommendation_id == Recommendation.id) & Vote.is_active.is_(True))
        .where(Recommendation.archived_at.is_(None))
        .group_by(Recommendation.id)
        .order_by(func.count(Vote.id).desc(), Recommendation.submitted_at.asc(), Recommendation.id.asc())
    ).all()
    return [{"recommendation": recommendation, "vote_count": vote_count} for recommendation, vote_count in rows]


def send_confirmation(db: Session, member, action: str, subject: str, body: str) -> bool:
    """Attempt SMTP after the business action commits; persist failures without undoing it."""
    failure: str | None = None
    try:
        if not settings.smtp_host or not settings.smtp_from:
            raise RuntimeError("Email delivery is not configured")
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = member.email
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except Exception as exc:  # keep the already-committed business operation intact
        failure = f"{type(exc).__name__}: {exc}"[:500]

    if failure:
        db.add(
            EmailFailure(
                member_id=member.id,
                recipient=member.email,
                action=action,
                error_summary=failure,
            )
        )
        db.commit()
        return False
    return True


def archive_recommendation(db: Session, recommendation: Recommendation, now: datetime) -> None:
    recommendation.archived_at = now
    db.execute(
        Vote.__table__.update()
        .where(Vote.recommendation_id == recommendation.id, Vote.is_active.is_(True))
        .values(is_active=False)
    )
