from sqlalchemy import Column, Integer, String, TIMESTAMP
from sqlalchemy.orm import relationship
from .base import Base
from .association import user_role_table
from datetime import datetime

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False)
    full_name = Column(String(200), nullable=True)
    email = Column(String(200), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True)
    address = Column(String(255), nullable=True)
    created_at = Column(TIMESTAMP, default=datetime.now)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)

    # Relationships roles
    roles = relationship('Role', secondary=user_role_table, back_populates='users')
    transactions = relationship('Transaction', back_populates='user', cascade='all, delete-orphan')

    # Check user roles

    def has_role(self, role_name):
        return any(role.name == role_name for role in self.roles)