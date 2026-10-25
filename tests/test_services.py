from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import EmailFailure, Member, Recommendation, Vote
from app.services import archive_recommendation, list_recommendations_with_votes, send_confirmation, voting_window


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def add_member(db: Session, email: str) -> Member:
    member = Member(name="Reader", email=email, favorite_genre="Fiction", google_subject=email)
    db.add(member)
    db.flush()
    return member


def add_book(db: Session, member: Member, title: str) -> Recommendation:
    book = Recommendation(
        member_id=member.id,
        title=title,
        author="A. Writer",
        genre="Fiction",
        description="A short description.",
    )
    db.add(book)
    db.flush()
    return book


def test_voting_window_is_closed_before_first_opening():
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert voting_window(first - timedelta(microseconds=1)) is None


def test_voting_window_repeats_every_fourteen_days():
    first = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert voting_window(first) == (first, first + timedelta(days=14))
    next_start = first + timedelta(days=14)
    assert voting_window(next_start) == (next_start, next_start + timedelta(days=14))


def test_results_count_active_votes_and_sort_descending(db: Session):
    owner = add_member(db, "owner@example.com")
    reader_one = add_member(db, "one@example.com")
    reader_two = add_member(db, "two@example.com")
    first = add_book(db, owner, "First book")
    second = add_book(db, reader_one, "Second book")
    db.add_all([
        Vote(member_id=reader_one.id, recommendation_id=first.id, is_active=True),
        Vote(member_id=reader_two.id, recommendation_id=first.id, is_active=True),
        Vote(member_id=owner.id, recommendation_id=second.id, is_active=True),
    ])
    db.commit()

    results = list_recommendations_with_votes(db)
    assert [(item["recommendation"].title, item["vote_count"]) for item in results] == [
        ("First book", 2),
        ("Second book", 1),
    ]


def test_database_rejects_more_than_one_active_vote_per_member(db: Session):
    member = add_member(db, "reader@example.com")
    first_owner = add_member(db, "first@example.com")
    second_owner = add_member(db, "second@example.com")
    first = add_book(db, first_owner, "First book")
    second = add_book(db, second_owner, "Second book")
    db.add(Vote(member_id=member.id, recommendation_id=first.id, is_active=True))
    db.commit()
    db.add(Vote(member_id=member.id, recommendation_id=second.id, is_active=True))
    with pytest.raises(IntegrityError):
        db.commit()


def test_inactive_archived_votes_do_not_count(db: Session):
    member = add_member(db, "reader@example.com")
    book = add_book(db, member, "Archived favorite")
    db.add(Vote(member_id=member.id, recommendation_id=book.id, is_active=False))
    book.archived_at = datetime.now(timezone.utc)
    db.commit()
    assert list_recommendations_with_votes(db) == []


def test_archiving_recommendation_deactivates_its_votes(db: Session):
    owner = add_member(db, "owner@example.com")
    voter = add_member(db, "voter@example.com")
    book = add_book(db, owner, "Old recommendation")
    vote = Vote(member_id=voter.id, recommendation_id=book.id, is_active=True)
    db.add(vote)
    db.commit()

    archive_recommendation(db, book, datetime.now(timezone.utc))
    db.commit()

    assert book.archived_at is not None
    assert vote.is_active is False
    assert list_recommendations_with_votes(db) == []


def test_email_failure_is_recorded_without_rolling_back_member(db: Session):
    member = add_member(db, "reader@example.com")
    db.commit()

    sent = send_confirmation(db, member, "registration", "Welcome", "Hello")

    assert sent is False
    assert db.get(Member, member.id) is not None
    assert db.query(EmailFailure).count() == 1