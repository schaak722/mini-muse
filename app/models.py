from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_VIEWER = "viewer"

ROLE_CHOICES = [ROLE_ADMIN, ROLE_USER, ROLE_VIEWER]

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    name = db.Column(db.String(120), nullable=False, default="User")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_viewer(self) -> bool:
        return self.role == ROLE_VIEWER

    # Flask-Login uses this to decide if user account is active
    def is_active_user(self) -> bool:
        return bool(self.is_active)

    def get_id(self):
        return str(self.id)

class Item(db.Model):
    __tablename__ = "items"

    id = db.Column(db.Integer, primary_key=True)

    sku = db.Column(db.String(80), unique=True, index=True, nullable=False)
    description = db.Column(db.String(255), nullable=False)

    brand = db.Column(db.String(80), nullable=True)
    supplier = db.Column(db.String(120), nullable=True)

    colour = db.Column(db.String(80), nullable=True)
    size = db.Column(db.String(40), nullable=True)

    weight = db.Column(db.Numeric(10, 3), nullable=True)      # kg
    vat_rate = db.Column(db.Numeric(5, 2), nullable=False, default=18.00)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
