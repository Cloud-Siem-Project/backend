from sqlalchemy import Column, String
from app.db.base import Base
from app.models.base_model import BaseModelMixin


class User(Base, BaseModelMixin):
    __tablename__ = "users"

    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    role = Column(String(50), nullable=False, default="analyst")