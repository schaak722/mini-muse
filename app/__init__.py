from flask import Flask
from .extensions import db, login_manager
from .models import User, ROLE_ADMIN
from config import Config

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Create tables + bootstrap first admin if needed
with app.app_context():
    from . import models  # noqa: F401
    db.create_all()
    _bootstrap_admin_if_needed(app)

    # Blueprints
    from .auth.routes import auth_bp
    from .admin.routes import admin_bp
    from .main.routes import main_bp
    from .catalog.routes import catalog_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(catalog_bp)

    # Simple health endpoint for Koyeb checks
    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app

def _bootstrap_admin_if_needed(app: Flask):
    """
    If the DB has no users, create a first admin user from env vars.
    This runs on startup and will only create a user once.
    """
    from .extensions import db
    from .models import User

    if User.query.count() > 0:
        return

    email = app.config.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = app.config.get("BOOTSTRAP_ADMIN_PASSWORD", "")
    name = app.config.get("BOOTSTRAP_ADMIN_NAME", "Admin")

    if not email or not password:
        # No bootstrap info provided; leave DB empty.
        return

    admin = User(email=email, name=name, role=ROLE_ADMIN, is_active=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

