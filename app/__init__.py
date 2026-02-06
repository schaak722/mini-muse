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
    from .routes import routes_bp
    from .api import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # ADD THIS TEMPORARY ROUTE HERE (BEFORE return app)
    @app.route("/run-migration-now")
    def run_migration_now():
        try:
            from flask_migrate import upgrade
            upgrade()
            return "✅ Migration completed! Delete this route now for security."
        except Exception as e:
            return f"❌ Migration failed: {str(e)}"

    return app
