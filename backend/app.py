from flask import Flask
from config.config import load_config
from routes.auth_routes import auth_bp
from routes.user_routes import user_bp
from routes.client_routes import client_bp
from routes.history_routes import history_bp
from routes.portfolio_routes import portfolio_bp
from routes.statistics_routes import statistics_bp

app = Flask(__name__)

# Carregar configurações do .env
config = load_config()
app.secret_key = config['SECRET_KEY']

# Configurar CORS
from flask_cors import CORS
CORS(app, supports_credentials=True, origins=["http://localhost:3000"])

# Registrar blueprints
app.register_blueprint(auth_bp, url_prefix='/')
app.register_blueprint(user_bp, url_prefix='/')
app.register_blueprint(client_bp, url_prefix='/')
app.register_blueprint(history_bp, url_prefix='/')
app.register_blueprint(portfolio_bp, url_prefix='/')
app.register_blueprint(statistics_bp, url_prefix='/')

if __name__ == '__main__':
    app.run(debug=True)