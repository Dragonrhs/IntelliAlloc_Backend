from flask import Flask
from config.config import load_config
from routes.auth_routes import auth_bp
from routes.user_routes import user_bp
from routes.client_routes import client_bp
from routes.history_routes import history_bp
from routes.portfolio_routes import portfolio_bp
from routes.statistics_routes import statistics_bp
from routes.ativos_routes import ativos_bp
from routes.parametros_routes import parametros_bp
from routes.ia_routes import ia_bp
from flask_cors import CORS
from middleware.auth import token_required

app = Flask(__name__)

# Carregar configurações do .env
config = load_config()
app.secret_key = config['SECRET_KEY']

# Configurar CORS com todas as opções necessárias
CORS(app, 
     resources={r"/*": {
         "origins": ["http://localhost:3000"],
         "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
         "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
         "expose_headers": ["Content-Type", "Authorization"],
         "supports_credentials": True,
         "max_age": 3600
     }})

# Registrar blueprints
app.register_blueprint(auth_bp, url_prefix='/')
app.register_blueprint(user_bp, url_prefix='/')
app.register_blueprint(client_bp, url_prefix='/')
app.register_blueprint(history_bp, url_prefix='/')
app.register_blueprint(portfolio_bp, url_prefix='/')
app.register_blueprint(statistics_bp, url_prefix='/')
app.register_blueprint(ativos_bp, url_prefix='/')
app.register_blueprint(parametros_bp, url_prefix='/')
app.register_blueprint(ia_bp)

if __name__ == '__main__':
    app.run(debug=True)