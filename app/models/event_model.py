from sqlalchemy import Column, String, Text
from app.db.base import Base
from app.models.base_model import BaseModelMixin


class Event(Base, BaseModelMixin):
    __tablename__ = "events"

    event_source = Column(String(100), nullable=False)
    event_type = Column(String(100), nullable=False)
    event_name = Column(String(150), nullable=True)
    source_ip = Column(String(50), nullable=True)
    destination_ip = Column(String(50), nullable=True)
    user_identity = Column(String(150), nullable=True)
    resource = Column(String(255), nullable=True)
    region = Column(String(50), nullable=True)
    account_id = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True)
    severity = Column(String(50), nullable=False, default="low")
    raw_log = Column(Text, nullable=True)