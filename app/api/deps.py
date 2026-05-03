"""
deps.py — FastAPI dependencies shared across routes.

get_current_user resolves the X-Auth-Token header to a User ORM object.
It never accepts user_id from the request body, path, or query parameters.
"""

from datetime import datetime

from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.session_token import SessionToken


def get_current_user(
    x_auth_token: str = Header(..., alias="X-Auth-Token"),
    db: Session = Depends(get_db),
) -> User:
    session = db.query(SessionToken).filter(SessionToken.token == x_auth_token).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
        )

    # SQLite stores naive datetimes; compare without timezone.
    if datetime.utcnow() > session.expires_at:
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired — please log in again",
        )

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
