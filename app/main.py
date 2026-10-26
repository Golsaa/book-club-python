from __future__ import annotations

import secrets
from datetime import datetime, timezone
from hmac import compare_digest

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .models import AppSettings, EmailFailure, Genre, Member, Recommendation, Vote
from .services import archive_recommendation, get_app_settings, get_genres, list_recommendations_with_votes, send_confirmation, voting_window


app = FastAPI(title="Book Club")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.cookie_secure,
    same_site="lax",
    session_cookie="bookclub_session",
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        get_app_settings(db)
        get_genres(db)
        db.commit()


def _csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Your form expired. Please reload and try again.")


def _current_member(request: Request, db: Session) -> Member | None:
    member_id = request.session.get("member_id")
    return db.get(Member, member_id) if member_id else None


def _page(request: Request, name: str, db: Session, **context) -> HTMLResponse:
    member = _current_member(request, db)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "member": member,
            "csrf_token": _csrf(request),
            "genres": get_genres(db),
            "is_admin": bool(member and member.email.lower() in settings.admin_emails),
            "flash": request.session.pop("flash", None),
            **context,
        },
    )


def _flash_redirect(request: Request, message: str, url: str = "/") -> RedirectResponse:
    request.session["flash"] = message
    return RedirectResponse(url, status_code=303)


def _require_member(request: Request, db: Session) -> Member:
    member = _current_member(request, db)
    if member is None:
        raise HTTPException(status_code=401, detail="Please sign in first.")
    return member


def _require_admin(request: Request, db: Session) -> Member:
    member = _require_member(request, db)
    if member.email.lower() not in settings.admin_emails:
        raise HTTPException(status_code=403, detail="Admin access is required.")
    return member


def _clean(value: str, maximum: int) -> str:
    return " ".join(value.strip().split())[:maximum]


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    window = voting_window(db=db)
    member = _current_member(request, db)
    current_vote = None
    own_recommendation = None
    if member:
        current_vote = db.scalar(select(Vote).where(Vote.member_id == member.id, Vote.is_active.is_(True)))
        own_recommendation = db.scalar(
            select(Recommendation).where(
                Recommendation.member_id == member.id, Recommendation.archived_at.is_(None)
            )
        )
    return _page(
        request,
        "home.html",
        db,
        entries=list_recommendations_with_votes(db),
        vote_window=window,
        current_vote=current_vote,
        own_recommendation=own_recommendation,
    )


@app.get("/auth/login")
async def login(request: Request):
    if not settings.google_client_id or not settings.google_client_secret:
        request.session["flash"] = "Google sign-in is not configured yet. Add the Google OAuth settings to .env."
        return RedirectResponse("/", status_code=303)
    return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return _flash_redirect(request, "Google sign-in could not be completed. Please try again.")
    claims = token.get("userinfo") or {}
    email = str(claims.get("email", "")).strip().lower()
    subject = str(claims.get("sub", ""))
    if not subject or not email or claims.get("email_verified") is not True:
        return _flash_redirect(request, "Google did not provide a verified email address.")
    member = db.scalar(select(Member).where(Member.google_subject == subject))
    if member:
        request.session.clear()
        request.session["member_id"] = member.id
        return RedirectResponse("/", status_code=303)
    existing_email = db.scalar(select(Member).where(Member.email == email))
    if existing_email:
        return _flash_redirect(request, "That email is already linked to another account. Contact the club organizer.")
    request.session["pending_google"] = {
        "subject": subject,
        "email": email,
        "name": _clean(str(claims.get("name", "")), 120),
    }
    return RedirectResponse("/register", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def registration_form(request: Request, db: Session = Depends(get_db)):
    pending = request.session.get("pending_google")
    if not pending:
        return _flash_redirect(request, "Sign in with Google before completing registration.")
    return _page(request, "register.html", db, pending=pending)


@app.post("/register")
def register(
    request: Request,
    name: str = Form(...),
    favorite_genre: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf_token)
    pending = request.session.get("pending_google")
    if not pending:
        return _flash_redirect(request, "Your sign-in expired. Please sign in again.")
    name = _clean(name, 120)
    if not name or favorite_genre not in get_genres(db):
        return _page(request, "register.html", db, pending=pending, error="Enter a name and choose a listed favorite genre.")
    member = Member(
        name=name,
        email=pending["email"].lower(),
        favorite_genre=favorite_genre,
        google_subject=pending["subject"],
    )
    try:
        db.add(member)
        db.commit()
        db.refresh(member)
    except IntegrityError:
        db.rollback()
        return _flash_redirect(request, "An account with this Google identity or email already exists. Please sign in.")
    request.session.clear()
    request.session["member_id"] = member.id
    sent = send_confirmation(db, member, "registration", "Welcome to the Book Club", f"Hi {member.name},\n\nYour book club registration is complete.")
    message = "Registration complete." if sent else "Registration complete, but the confirmation email could not be sent; the failure has been recorded."
    return _flash_redirect(request, message)


@app.post("/auth/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    _check_csrf(request, csrf_token)
    request.session.clear()
    request.session["flash"] = "You have signed out."
    return RedirectResponse("/", status_code=303)


@app.post("/recommendations")
def save_recommendation(
    request: Request,
    title: str = Form(...),
    author: str = Form(...),
    genre: str = Form(...),
    description: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf_token)
    member = _current_member(request, db)
    if not member:
        return _flash_redirect(request, "Please sign in before recommending a book.")
    title, author = _clean(title, 200), _clean(author, 160)
    description = description.strip()
    if not title or not author or not description or len(description) > 2000 or genre not in get_genres(db):
        return _flash_redirect(request, "Complete every field, use a listed genre, and keep the description under 2,000 characters.")
    try:
        previous = db.scalar(
            select(Recommendation)
            .where(Recommendation.member_id == member.id, Recommendation.archived_at.is_(None))
            .with_for_update()
        )
        now = datetime.now(timezone.utc)
        if previous:
            archive_recommendation(db, previous, now)
            db.flush()
        recommendation = Recommendation(
            member_id=member.id, title=title, author=author, genre=genre, description=description
        )
        db.add(recommendation)
        db.commit()
    except IntegrityError:
        db.rollback()
        return _flash_redirect(request, "Your recommendation could not be saved. Please try again.")
    sent = send_confirmation(db, member, "recommendation", "Book recommendation received", f"Your recommendation for {title} by {author} was saved.")
    message = "Your recommendation was saved." if sent else "Your recommendation was saved, but its confirmation email failed; the failure has been recorded."
    return _flash_redirect(request, message)


@app.post("/votes/{recommendation_id}")
def cast_vote(
    recommendation_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf_token)
    member = _current_member(request, db)
    if not member:
        return _flash_redirect(request, "Please sign in before voting.")
    window = voting_window(db=db)
    if window is None or not (window[0] <= datetime.now(timezone.utc) < window[1]):
        return _flash_redirect(request, "Voting is currently closed.")
    recommendation = db.get(Recommendation, recommendation_id)
    if not recommendation or recommendation.archived_at is not None:
        return _flash_redirect(request, "That recommendation is no longer available.")
    try:
        previous = db.scalar(
            select(Vote).where(Vote.member_id == member.id, Vote.is_active.is_(True)).with_for_update()
        )
        if previous and previous.recommendation_id == recommendation.id:
            return _flash_redirect(request, "Your vote is already recorded for that book.")
        if previous:
            previous.is_active = False
            db.flush()
        db.add(Vote(member_id=member.id, recommendation_id=recommendation.id, is_active=True))
        db.commit()
    except IntegrityError:
        db.rollback()
        return _flash_redirect(request, "Your vote could not be saved. Please try again.")
    sent = send_confirmation(db, member, "vote", "Your book-club vote", f"Your vote for {recommendation.title} by {recommendation.author} was recorded.")
    message = "Your vote was recorded." if sent else "Your vote was recorded, but its confirmation email failed; the failure has been recorded."
    return _flash_redirect(request, message)


@app.get("/api/results")
def results_api(db: Session = Depends(get_db)):
    return JSONResponse(
        [
            {
                "id": entry["recommendation"].id,
                "title": entry["recommendation"].title,
                "author": entry["recommendation"].author,
                "genre": entry["recommendation"].genre,
                "description": entry["recommendation"].description,
                "vote_count": entry["vote_count"],
            }
            for entry in list_recommendations_with_votes(db)
        ]
    )


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, db: Session = Depends(get_db)):
    member = _current_member(request, db)
    if not member:
        return _flash_redirect(request, "Sign in to view history.")
    archived = db.execute(
        select(Recommendation, func.count(Vote.id))
        .outerjoin(Vote, Vote.recommendation_id == Recommendation.id)
        .where(Recommendation.member_id == member.id, Recommendation.archived_at.is_not(None))
        .group_by(Recommendation.id)
        .order_by(Recommendation.archived_at.desc())
    ).all()
    past_votes = db.execute(
        select(Vote, Recommendation)
        .join(Recommendation)
        .where(Vote.member_id == member.id, Vote.is_active.is_(False))
        .order_by(Vote.cast_at.desc())
    ).all()
    return _page(request, "history.html", db, archived=archived, past_votes=past_votes)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    _require_admin(request, db)
    config = get_app_settings(db)
    first_start = config.voting_first_start
    if first_start.tzinfo is None:
        first_start = first_start.replace(tzinfo=timezone.utc)
    return _page(
        request,
        "admin.html",
        db,
        voting_first_start=first_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        voting_period_days=config.voting_period_days,
        genre_text="\n".join(get_genres(db)),
    )


@app.post("/admin/settings")
def update_admin_settings(
    request: Request,
    voting_first_start: str = Form(...),
    voting_period_days: int = Form(...),
    genres: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf_token)
    _require_admin(request, db)

    try:
        first_start = datetime.fromisoformat(voting_first_start)
        if first_start.tzinfo is None:
            first_start = first_start.replace(tzinfo=timezone.utc)
        first_start = first_start.astimezone(timezone.utc)
    except ValueError:
        return _flash_redirect(request, "Enter a valid voting start date and time in UTC.", "/admin")

    genre_names = [" ".join(line.split()) for line in genres.splitlines()]
    genre_names = [name for name in genre_names if name]
    folded_names = [name.casefold() for name in genre_names]
    if not 1 <= voting_period_days <= 366:
        return _flash_redirect(request, "Voting periods must be between 1 and 366 days.", "/admin")
    if (
        not genre_names
        or len(genre_names) > 30
        or any(len(name) > 40 for name in genre_names)
        or len(set(folded_names)) != len(folded_names)
    ):
        return _flash_redirect(
            request,
            "Enter 1–30 unique genre names, with each name no longer than 40 characters.",
            "/admin",
        )

    config = get_app_settings(db)
    config.voting_first_start = first_start
    config.voting_period_days = voting_period_days
    db.execute(delete(Genre))
    db.flush()
    db.add_all(Genre(name=name, position=index) for index, name in enumerate(genre_names))
    db.commit()
    return _flash_redirect(request, "Voting schedule and genres saved.", "/admin")
