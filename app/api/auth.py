from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.models import Role, User
import bcrypt
from pydantic import BaseModel
from app.database import get_db

router = APIRouter()

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    phone: str | None = None
    address: str | None = None

class UserResponse(BaseModel):
    id: int
    username: str | None
    name: str
    email: str
    roles: list[str]


# login endpoint
def authenticate_user(email: str, password: str, db: Session) -> UserResponse:
    # Keep request schema stable but allow email or username in this field.
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
    
    roles = [role.name.strip() for role in user.roles]
    
    return UserResponse(
        id=user.id,
        username=user.username,
        name=user.username,
        email=user.email,
        roles=roles
    )


@router.post("/login", response_model=UserResponse)
def login_post(
    request: LoginRequest = Body(...),
    db: Session = Depends(get_db),
):
    if request is not None:
        return authenticate_user(request.email, request.password, db)

    raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide email and password in JSON body or query params",
        )


@router.get("/login", response_model=UserResponse)
def login_get(
    email: str = Query(...),
    password: str = Query(...),
    db: Session = Depends(get_db),
):
    return authenticate_user(email, password, db)

@router.post("/register", response_model=UserResponse)
def register(
    request: RegisterRequest = Body(...),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == request.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
    if db.query(User).filter(User.username == request.username).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already taken")
    
    hashed = bcrypt.hashpw(request.password.encode(), bcrypt.gensalt()).decode()

    is_first_user = db.query(User).count() == 0
    role_name = "admin" if is_first_user else "user"

    role = db.query(Role).filter(Role.name == role_name).first()
    if not role:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Role not found in database")
    
    new_user = User(
        username=request.username,
        email=request.email,
        password=hashed,
        phone=request.phone or "",
        address=request.address or "",
    )

    new_user.roles.append(role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        name=new_user.username,
        email=new_user.email,
        roles=[role.name for role in new_user.roles]
    )