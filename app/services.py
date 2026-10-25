from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import EmailFailure, Recommendation, Vote


def voting_window(now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """Return the current UTC voting window, or None before the first opening."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    first_start = settings.voting_first_start
    length = timedelta(days=settings.voting_period_days)
    if now < first_start:
        return None
    periods_elapsed = (now - first_start) // length
    start = first_start + periods_elapsed * length
    return start, start + length


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