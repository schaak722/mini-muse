from flask import Flask, jsonify
from .config import Config
from .extensions import db, migrate, login_manager

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    from .cli import register_cli
    register_cli(app)

    from .auth import auth_bp
    from .api import api_bp
    from .routes import routes_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)                 # UI routes
    app.register_blueprint(api_bp, url_prefix="/api") # optional JSON


    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app
