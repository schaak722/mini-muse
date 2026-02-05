import os
from flask import Flask
from .extensions import db
from .models import User

def register_cli(app: Flask) -> None:
    @app.cli.command("create-admin")
    def create_admin():
        email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        password = os.getenv("ADMIN_PASSWORD", "")
        if not email or not password:
            print("Set ADMIN_EMAIL and ADMIN_PASSWORD env vars first.")
            return

        user = db.session.query(User).filter_by(email=email).first()
        if user:
            print("Admin already exists:", email)
            return

        u = User(email=email, first_name="Admin", last_name="User", role="ADMIN")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        print("Created admin:", email)

