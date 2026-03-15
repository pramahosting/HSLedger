from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import relationship
from .base import Base
from .association import user_role_table, role_permission_table


class Role(Base):
    __tablename__ = 'roles'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)

    # Relationships
    users = relationship('User', secondary=user_role_table, back_populates='roles')
    permissions = relationship('Permission', secondary=role_permission_table, back_populates='roles')