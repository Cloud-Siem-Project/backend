from sqlalchemy import Column, String, Integer, ForeignKey
from app.db.base import Base
from app.models.base_model import BaseModelMixin


class Alert(Base, BaseModelMixin):
    __tablename__ = "alerts"

    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    severity = Column(String(50), nullable=False, default="low")
    status = Column(String(50), nullable=False, default="open")