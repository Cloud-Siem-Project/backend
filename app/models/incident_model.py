from sqlalchemy import Column, String, Integer
from app.db.base import Base
from app.models.base_model import BaseModelMixin


class Incident(Base, BaseModelMixin):
    __tablename__ = "incidents"

    title = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    severity = Column(String(50), nullable=False, default="low")
    status = Column(String(50), nullable=False, default="open")
    risk_score = Column(Integer, nullable=False, default=0)