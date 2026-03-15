from sqlalchemy import Table, Column, Integer, ForeignKey
from .base import Base

# Relation User-Role
user_role_table = Table('user_roles',
                        Base.metadata,
                        Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
                        Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True)
                        )

# Relation Role-Permission
role_permission_table = Table('role_permissions',
                              Base.metadata,
                              Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
                              Column('permission_id', Integer, ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True)
                              )