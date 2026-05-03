import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from app.models import Role, User
from app.models.session_token import SessionToken
import bcrypt
from pydantic import BaseModel
from app.database import get_db

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    full_name: str | None = None
    email: str
    password: str
    phone: str | None = None
    address: str | None = None
    role: str | None = None  # Optional, only honored for first user or by admin

class UserResponse(BaseModel):
    id: int
    username: str | None
    name: str
    email: str
    roles: list[str]
    permissions: list[str]


class LoginResponse(BaseModel):
    """Wraps user data with a session token returned on successful login."""
    token: str
    user: UserResponse


def build_user_response(user: User) -> UserResponse:
    roles = [role.name.strip() for role in user.roles]

    # Flatten role permissions and remove duplicates.
    permissions = sorted(
        {
            perm.name.strip()
            for role in user.roles
            for perm in role.permissions
            if perm.name
        }
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        name=user.full_name or user.username,
        email=user.email,
        roles=roles,
        permissions=permissions,
    )


def ensure_users_full_name_column(db: Session):
    # Lightweight schema backfill for existing SQLite DBs created before full_name existed.
    engine_name = db.bind.dialect.name if db.bind else ""
    if engine_name != "sqlite":
        return

    users_table = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    ).fetchone()
    if not users_table:
        return

    columns = db.execute(text("PRAGMA table_info(users)")).fetchall()
    column_names = {row[1] for row in columns}
    if "full_name" not in column_names:
        db.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(200)"))
        db.commit()


# login endpoint
def authenticate_user(email: str, password: str, db: Session) -> User:
    """Validate credentials and return the User ORM object (raises 401 on failure)."""
    ensure_users_full_name_column(db)

    login_value = email.strip()
    user = (
        db.query(User)
        .filter(or_(User.email == login_value, User.username == login_value))
        .first()
    )

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not bcrypt.checkpw(password.encode(), user.password.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return user


def _create_session_token(user: User, db: Session) -> str:
    """Generate a UUID session token, persist it, and return the token string."""
    token = str(uuid.uuid4())
    db.add(SessionToken(token=token, user_id=user.id, expires_at=SessionToken.make_expiry()))
    db.commit()
    return token


@router.post("/login", response_model=LoginResponse)
def login_post(
    request: LoginRequest = Body(...),
    db: Session = Depends(get_db),
):
    user  = authenticate_user(request.email, request.password, db)
    token = _create_session_token(user, db)
    return LoginResponse(token=token, user=build_user_response(user))


@router.get("/login", response_model=LoginResponse)
def login_get(
    email:    str     = Query(...),
    password: str     = Query(...),
    db:       Session = Depends(get_db),
):
    user  = authenticate_user(email, password, db)
    token = _create_session_token(user, db)
    return LoginResponse(token=token, user=build_user_response(user))

@router.post("/register", response_model=UserResponse)
def register(
    request: RegisterRequest = Body(...),
    db: Session = Depends(get_db),
):
    ensure_users_full_name_column(db)

    if db.query(User).filter(User.email == request.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    if db.query(User).filter(User.username == request.username).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")

    hashed = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()
    is_first_user = db.query(User).count() == 0

    # Only allow role selection for first user or if admin is registering
    allowed_roles = {r.name for r in db.query(Role).all()}
    requested_role = (request.role or "user").strip().lower()
    if is_first_user:
        role_name = requested_role if requested_role in allowed_roles else "admin"
    else:
        # Check if current user is admin (future: use auth context)
        # For now, only allow 'user' role for non-first-user signups
        role_name = "user"

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Role not found in database")

    new_user = User(
        username=request.username,
        full_name=(request.full_name or request.username),
        email=request.email,
        password=hashed,
        phone=request.phone or "",
        address=request.address or "",
    )
    new_user.roles.append(role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return build_user_response(new_user)