from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error
import json
import re
import os
import requests
import time
from dotenv import load_dotenv
from functools import wraps
import threading
from datetime import datetime, timedelta
from google import generativeai as genai

load_dotenv()

portfolio_bp = Blueprint('portfolio', __name__)

# Configurar o Gemini com a chave da API do arquivo .env
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise Exception("GEMINI_API_KEY não encontrada nas variáveis de ambiente")

genai.configure(api_key=GEMINI_API_KEY)

# Rate limiting
class RateLimiter:
    def __init__(self, max_requests, time_window):
        self.max_requests = max_requests
        self.time_window = time_window  # em segundos
        self.requests = []
        self.lock = threading.Lock()

    def can_make_request(self):
        now = datetime.now()
        with self.lock:
            # Remover requisições antigas
            self.requests = [req_time for req_time in self.requests 
                           if now - req_time < timedelta(seconds=self.time_window)]
            
            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False

    def wait_for_next_slot(self):
        while not self.can_make_request():
            time.sleep(1)

# Criar instância do rate limiter (60 requisições por minuto para o Gemini)
gemini_limiter = RateLimiter(max_requests=60, time_window=60)

def retry_with_backoff(max_retries=3, initial_delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.RequestException as e:
                    if retry == max_retries - 1:  # Última tentativa
                        raise
                    if e.response is not None and e.response.status_code == 429:
                        print(f"Rate limit atingido, aguardando {delay} segundos...")
                        time.sleep(delay)
                        delay *= 2  # Exponential backoff
                    else:
                        raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

def validate_portfolio_sum(carteiras, perfil):
    """Valida se a soma dos percentuais das bandas é 100% para cada tipo de banda"""
    carteiras_perfil = [c for c in carteiras if c['perfil'] == perfil]
    if not carteiras_perfil:
        return True, None

    # Validar cada tipo de banda
    for banda in ['banda_inferior', 'banda_neutra', 'banda_superior']:
        soma_percentuais = sum(float(c[banda] or 0) for c in carteiras_perfil)
        if abs(soma_percentuais - 100) > 0.01:  # Tolerância de 0.01%
            return False, banda
    return True, None

def validate_mes_format(mes):
    """Valida se o formato do mês está correto (YYYY-MM)"""
    pattern = r'^\d{4}-(0[1-9]|1[0-2])$'
    return bool(re.match(pattern, mes))

@portfolio_bp.route('/api/carteira/adicionar', methods=['POST'])
@token_required
def adicionar_carteira():
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem adicionar carteiras recomendadas'}), 403

    data = request.get_json()
    if not data or 'mes_referencia' not in data or 'carteiras' not in data:
        return jsonify({'error': 'Mês de referência e carteiras são obrigatórios'}), 400

    mes_referencia = data['mes_referencia']
    carteiras = data['carteiras']

    # Validar formato do mês
    if not validate_mes_format(mes_referencia):
        return jsonify({'error': 'Formato do mês inválido. Use o formato YYYY-MM'}), 400

    # Validar se todas as carteiras têm os campos necessários
    for carteira in carteiras:
        if not all(key in carteira for key in ['perfil', 'classe_ativo', 'banda_inferior', 'banda_neutra', 'banda_superior']):
            return jsonify({'error': 'Todos os campos da carteira são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Verificar se já existem carteiras para o mês especificado
        cursor.execute("SELECT COUNT(*) FROM carteira_recomendada WHERE mes_referencia = %s", (mes_referencia,))
        if cursor.fetchone()[0] > 0:
            return jsonify({'error': f'Já existem carteiras cadastradas para o mês {mes_referencia}'}), 400

        # Validar a soma dos percentuais para cada perfil
        for perfil in ['Conservador', 'Moderado', 'Sofisticado']:
            is_valid, banda_invalida = validate_portfolio_sum(carteiras, perfil)
            if not is_valid:
                return jsonify({
                    'error': f'A soma dos percentuais da {banda_invalida.replace("_", " ")} para o perfil {perfil} deve ser 100%'
                }), 400

        # Inserir cada carteira
        for carteira in carteiras:
            cursor.execute("""
                INSERT INTO carteira_recomendada 
                (mes_referencia, perfil, classe_ativo, banda_inferior, banda_neutra, banda_superior)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                mes_referencia,
                carteira['perfil'],
                carteira['classe_ativo'],
                carteira['banda_inferior'] or 0,
                carteira['banda_neutra'] or 0,
                carteira['banda_superior'] or 0
            ))

        connection.commit()

        return jsonify({
            'message': f'Carteiras do mês {mes_referencia} adicionadas com sucesso'
        }), 201

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao adicionar carteiras: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/api/carteira/editar/<mes>', methods=['PUT'])
@token_required
def editar_carteira_mes(mes):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem editar carteiras recomendadas'}), 403

    data = request.get_json()
    if not data or 'carteiras' not in data:
        return jsonify({'error': 'Dados da carteira são obrigatórios'}), 400

    carteiras = data['carteiras']
    
    # Validar se todas as carteiras têm os campos necessários
    for carteira in carteiras:
        if not all(key in carteira for key in ['perfil', 'classe_ativo', 'banda_inferior', 'banda_neutra', 'banda_superior']):
            return jsonify({'error': 'Todos os campos da carteira são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Primeiro, verificar se existem carteiras para o mês especificado
        cursor.execute("SELECT COUNT(*) FROM carteira_recomendada WHERE mes_referencia = %s", (mes,))
        if cursor.fetchone()[0] == 0:
            return jsonify({'error': f'Nenhuma carteira encontrada para o mês {mes}'}), 404

        # Validar a soma dos percentuais para cada perfil
        for perfil in ['Conservador', 'Moderado', 'Sofisticado']:
            is_valid, banda_invalida = validate_portfolio_sum(carteiras, perfil)
            if not is_valid:
                return jsonify({
                    'error': f'A soma dos percentuais da {banda_invalida.replace("_", " ")} para o perfil {perfil} deve ser 100%'
                }), 400

        # Atualizar cada carteira
        for carteira in carteiras:
            cursor.execute("""
                UPDATE carteira_recomendada 
                SET banda_inferior = %s,
                    banda_neutra = %s,
                    banda_superior = %s
                WHERE mes_referencia = %s 
                AND perfil = %s 
                AND classe_ativo = %s
            """, (
                carteira['banda_inferior'] or 0,
                carteira['banda_neutra'] or 0,
                carteira['banda_superior'] or 0,
                mes,
                carteira['perfil'],
                carteira['classe_ativo']
            ))

        connection.commit()

        return jsonify({
            'message': f'Carteiras do mês {mes} atualizadas com sucesso'
        }), 200

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao atualizar carteiras: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/api/carteira/meses', methods=['GET'])
@token_required
def get_meses_disponiveis():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT DISTINCT mes_referencia 
            FROM carteira_recomendada 
            ORDER BY mes_referencia DESC
        """)
        meses = cursor.fetchall()
        
        return jsonify({'meses': [mes['mes_referencia'] for mes in meses]}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar meses disponíveis: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/recommended-portfolio', methods=['POST'])
@token_required
def add_recommended_portfolio():
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem criar carteiras recomendadas'}), 403

    data = request.get_json()
    profile = data.get('profile')
    asset_classes = data.get('asset_classes')

    if not profile or not asset_classes:
        return jsonify({'error': 'Perfil e classes de ativos são obrigatórios'}), 400

    # Validar se a soma dos percentuais da banda neutra é 100%
    total_sum = sum(float(asset_class.get('banda_neutra', 0) or 0) for asset_class in asset_classes)
    if abs(total_sum - 100) > 0.01:  # Tolerância de 0.01%
        return jsonify({'error': 'A soma dos percentuais da banda neutra deve ser 100%'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        
        cursor.execute("""
            INSERT INTO recommended_portfolio (user_id, profile, asset_classes)
            VALUES (%s, %s, %s)
        """, (request.user_id, profile, json.dumps(asset_classes)))
        
        portfolio_id = cursor.lastrowid
        connection.commit()

        return jsonify({
            'message': 'Carteira recomendada adicionada com sucesso',
            'portfolio_id': portfolio_id
        }), 201

    except Error as e:
        return jsonify({'error': f'Erro ao adicionar carteira recomendada: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/recommended-portfolios', methods=['GET'])
@token_required
def get_recommended_portfolios():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, profile, asset_classes, created_at
            FROM recommended_portfolio
            ORDER BY created_at DESC
        """)
        portfolios = cursor.fetchall()
        
        for portfolio in portfolios:
            portfolio['asset_classes'] = json.loads(portfolio['asset_classes'])
        
        return jsonify({'portfolios': portfolios}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao listar carteiras recomendadas: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/api/carteira/<mes>', methods=['GET'])
@token_required
def get_carteira_mes(mes):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT perfil, classe_ativo, banda_inferior, banda_neutra, banda_superior
            FROM carteira_recomendada 
            WHERE mes_referencia = %s
            ORDER BY perfil, classe_ativo
        """, (mes,))
        
        carteiras = cursor.fetchall()
        
        if not carteiras:
            return jsonify({'error': f'Nenhuma carteira encontrada para o mês {mes}'}), 404

        return jsonify({'carteiras': carteiras}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar carteiras: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def call_gemini_api(prompt):
    try:
        model = genai.GenerativeModel('gemini-2.0-pro-exp-02-05')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Erro ao chamar API do Gemini: {str(e)}")
        raise

@portfolio_bp.route('/api/carteira/comparar', methods=['POST'])
@token_required
def comparar_carteiras():
    try:
        data = request.get_json()
        mes1 = data.get('mes1')
        mes2 = data.get('mes2')

        print(f"Comparando carteiras para os meses: {mes1} e {mes2}")

        if not mes1 or not mes2:
            return jsonify({'error': 'Meses não fornecidos'}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Erro ao conectar ao banco de dados'}), 500

        cur = conn.cursor(dictionary=True)

        # Buscar dados da primeira carteira
        cur.execute("""
            SELECT perfil, classe_ativo, 
                   CAST(banda_inferior AS FLOAT) as banda_inferior,
                   CAST(banda_neutra AS FLOAT) as banda_neutra,
                   CAST(banda_superior AS FLOAT) as banda_superior
            FROM carteira_recomendada
            WHERE mes_referencia = %s
            ORDER BY perfil, classe_ativo
        """, (mes1,))
        carteira1 = cur.fetchall()
        print(f"Dados carteira 1: {carteira1}")

        # Buscar dados da segunda carteira
        cur.execute("""
            SELECT perfil, classe_ativo, 
                   CAST(banda_inferior AS FLOAT) as banda_inferior,
                   CAST(banda_neutra AS FLOAT) as banda_neutra,
                   CAST(banda_superior AS FLOAT) as banda_superior
            FROM carteira_recomendada
            WHERE mes_referencia = %s
            ORDER BY perfil, classe_ativo
        """, (mes2,))
        carteira2 = cur.fetchall()
        print(f"Dados carteira 2: {carteira2}")

        cur.close()
        conn.close()

        if not carteira1:
            return jsonify({'error': f'Carteira não encontrada para o mês {mes1}'}), 404
        if not carteira2:
            return jsonify({'error': f'Carteira não encontrada para o mês {mes2}'}), 404

        # Formatar os dados das carteiras para melhor legibilidade
        def format_carteira(carteira):
            result = {}
            for item in carteira:
                perfil = item['perfil']
                if perfil not in result:
                    result[perfil] = []
                result[perfil].append({
                    'classe_ativo': item['classe_ativo'],
                    'banda_inferior': item['banda_inferior'],
                    'banda_neutra': item['banda_neutra'],
                    'banda_superior': item['banda_superior']
                })
            return result

        carteira1_formatada = format_carteira(carteira1)
        carteira2_formatada = format_carteira(carteira2)

        # Preparar dados para a API do Gemini
        prompt = f"""Compare as seguintes carteiras de investimentos e liste as principais mudanças de {mes1} para {mes2} de forma concisa:

Carteira {mes1}:
{json.dumps(carteira1_formatada, indent=2, ensure_ascii=False)}

Carteira {mes2}:
{json.dumps(carteira2_formatada, indent=2, ensure_ascii=False)}

Por favor, analise e liste apenas as principais mudanças em formato de tópicos curtos, focando em:
1. Mudanças significativas nas bandas (inferior, neutra e superior)
2. Alterações nas classes de ativos por perfil
3. Tendências gerais de alocação"""
        
        try:
            comparison = call_gemini_api(prompt)
            return jsonify({'comparison': comparison})

        except Exception as e:
            print(f"Erro na chamada à API do Gemini: {str(e)}")
            return jsonify({
                'error': 'Erro ao chamar API do Gemini. Por favor, tente novamente em alguns instantes.'
            }), 500

    except Exception as e:
        print(f"Erro ao comparar carteiras: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Erro ao processar a comparação das carteiras'}), 500 