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
from config.config import load_config
import pandas as pd
import logging
import numpy as np
from scipy.optimize import minimize
import traceback

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
    """Valida se a soma dos percentuais da banda neutra é 100%"""
    carteiras_perfil = [c for c in carteiras if c['perfil'] == perfil]
    if not carteiras_perfil:
        return True, None

    # Validar apenas a banda neutra
    soma_percentuais = sum(float(c['banda_neutra'] or 0) for c in carteiras_perfil)
    if abs(soma_percentuais - 100) > 0.01:  # Tolerância de 0.01%
        return False, 'banda_neutra'
    return True, None

def validate_mes_format(mes):
    """Valida se o formato do mês está correto (YYYY-MM)"""
    pattern = r'^\d{4}-(0[1-9]|1[0-2])$'
    return bool(re.match(pattern, mes))

@portfolio_bp.route('/api/carteira/adicionar', methods=['POST'])
@token_required
def adicionar_carteira(current_user=None):
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
def editar_carteira_mes(mes, current_user=None):
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
def get_meses_disponiveis(current_user=None):
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
def add_recommended_portfolio(current_user=None):
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
        """, (current_user, profile, json.dumps(asset_classes)))
        
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
def get_recommended_portfolios(current_user=None):
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
def get_carteira_mes(mes, current_user=None):
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
def comparar_carteiras(current_user=None):
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
        traceback.print_exc()
        return jsonify({'error': 'Erro ao processar a comparação das carteiras'}), 500

@portfolio_bp.route('/api/avaliacao-classe/<mes>', methods=['GET'])
@token_required
def get_avaliacao_classe_mes(mes, current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT classe_ativo, nota
            FROM avaliacao_classe_ativo 
            WHERE mes_referencia = %s
            ORDER BY classe_ativo
        """, (mes,))
        
        avaliacoes = cursor.fetchall()
        
        return jsonify({'avaliacoes': avaliacoes}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar avaliações: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/api/avaliacao-classe/adicionar', methods=['POST'])
@token_required
def adicionar_avaliacao_classe(current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem adicionar avaliações'}), 403

    data = request.get_json()
    if not data or 'mes_referencia' not in data or 'avaliacoes' not in data:
        return jsonify({'error': 'Mês de referência e avaliações são obrigatórios'}), 400

    mes_referencia = data['mes_referencia']
    avaliacoes = data['avaliacoes']

    # Validar formato do mês
    if not validate_mes_format(mes_referencia):
        return jsonify({'error': 'Formato do mês inválido. Use o formato YYYY-MM'}), 400

    # Validar se todas as avaliações têm os campos necessários
    for avaliacao in avaliacoes:
        if not all(key in avaliacao for key in ['classe_ativo', 'nota']):
            return jsonify({'error': 'Todos os campos da avaliação são obrigatórios'}), 400
        
        if not isinstance(avaliacao['nota'], int) or avaliacao['nota'] < -2 or avaliacao['nota'] > 2:
            return jsonify({'error': 'A nota deve ser um número inteiro entre -2 e 2'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Inserir cada avaliação
        for avaliacao in avaliacoes:
            cursor.execute("""
                INSERT INTO avaliacao_classe_ativo 
                (mes_referencia, classe_ativo, nota)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE nota = VALUES(nota)
            """, (
                mes_referencia,
                avaliacao['classe_ativo'],
                avaliacao['nota']
            ))

        connection.commit()

        return jsonify({
            'message': f'Avaliações do mês {mes_referencia} adicionadas com sucesso'
        }), 201

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao adicionar avaliações: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@portfolio_bp.route('/api/portfolio/gerar', methods=['POST'])
@token_required
def gerar_portfolio(current_user=None):
    """
    Gera um ou mais portfólios no Comdinheiro com todos os ativos do banco de dados
    
    Esta função:
    1. Busca todos os ativos aprovados no banco de dados
    2. Extrai os indexadores primários (ticker, ISIN ou CNPJ) conforme a classificação
    3. Envia os dados para a API do Comdinheiro para criar portfólios
    4. Se houver mais de 1000 ativos, cria múltiplos portfólios (ativos_Research, ativos_Research1, etc.)
    
    Parâmetros (JSON):
    - nome_portfolio: Nome do portfólio a ser criado (ignorado, sempre usa "ativos_Research")
    
    Retorno:
    - JSON com informações sobre os portfólios criados
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerar portfólios'}), 403
        
        # Nome base do portfólio
        nome_portfolio_base = "ativos_Research"
        
        # Conectar ao banco de dados
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Buscar todos os indexadores primários dos ativos
        query = """
            SELECT 
                a.id, a.nome, a.ticker, a.isin, a.cnpj,
                c.indexador_primario, c.tipo_indexador,
                a.status
            FROM 
                ativos a
            LEFT JOIN 
                ativo_classificacao c ON a.id = c.ativo_id
        """
        
        cursor.execute(query)
        ativos = cursor.fetchall()
        
        # Buscar ativos prioritários
        try:
            cursor.execute("SELECT codigo, descricao, ordem FROM ativos_prioritarios ORDER BY ordem")
            ativos_prioritarios = cursor.fetchall()
            print(f"Ativos prioritários encontrados: {len(ativos_prioritarios)}")
            for ativo in ativos_prioritarios:
                print(f"  - {ativo['codigo']} ({ativo['descricao']})")
        except Exception as e:
            ativos_prioritarios = []
            print(f"Erro ao buscar ativos prioritários: {str(e)}")
        
        # Fechar conexão com o banco
        cursor.close()
        connection.close()
        
        # Verificar se encontrou ativos
        if not ativos:
            return jsonify({'error': 'Nenhum ativo encontrado para gerar o portfólio'}), 404
        
        print(f"Total de ativos encontrados: {len(ativos)}")
        
        # Contadores para estatísticas
        count_cri_cdca_cra = 0
        count_deb = 0
        count_outros = 0
        count_prioritarios = 0
        status_counts = {
            'Aprovado': 0,
            'Em Analise': 0,
            'Reprovado': 0
        }
        
        # Lista de todos os códigos de ativos para o portfólio
        todos_codigos = []
        
        # Primeiro adicionar os ativos prioritários, se existirem
        for ativo_prioritario in ativos_prioritarios:
            codigo = ativo_prioritario['codigo']
            todos_codigos.append(codigo)
            count_prioritarios += 1
            print(f"Adicionando ativo prioritário: {codigo}")
        
        # Processar os demais ativos
        for ativo in ativos:
            # Contabilizar o status
            status = ativo['status']
            if status in status_counts:
                status_counts[status] += 1
            else:
                status_counts[status] = 1
                
            codigo = None
            
            # Usar o indexador primário conforme a classificação
            if ativo['tipo_indexador'] == 'ticker' and ativo['ticker']:
                codigo = ativo['ticker']
            elif ativo['tipo_indexador'] == 'isin' and ativo['isin']:
                codigo = ativo['isin']
            elif ativo['tipo_indexador'] == 'cnpj' and ativo['cnpj']:
                codigo = ativo['cnpj']
            # Se não tiver classificação, usar ticker, isin ou cnpj na ordem
            elif ativo['ticker']:
                codigo = ativo['ticker']
            elif ativo['isin']:
                codigo = ativo['isin']
            elif ativo['cnpj']:
                codigo = ativo['cnpj']
            
            # Adicionar prefixos conforme regras
            if codigo:
                nome = ativo['nome'] if ativo['nome'] else ''
                
                # Adicionar prefixo CETIP_ para CRI, CDCA ou CRA
                if nome.startswith('CRI') or nome.startswith('CDCA') or nome.startswith('CRA'):
                    codigo_original = codigo
                    codigo = f"CETIP_{codigo}"
                    print(f"Adicionando prefixo CETIP_: {codigo_original} -> {codigo} (Status: {status})")
                    count_cri_cdca_cra += 1
                # Adicionar prefixo DEB: para DEB
                elif nome.startswith('DEB'):
                    codigo_original = codigo
                    codigo = f"DEB:{codigo}"
                    print(f"Adicionando prefixo DEB:: {codigo_original} -> {codigo} (Status: {status})")
                    count_deb += 1
                else:
                    count_outros += 1
                
                todos_codigos.append(codigo)
        
        # Verificar se há ativos para enviar
        if not todos_codigos:
            return jsonify({'error': 'Nenhum ativo com identificador válido encontrado'}), 400
        
        print(f"Estatísticas de ativos:")
        print(f"- CRI/CDCA/CRA (prefixo CETIP_): {count_cri_cdca_cra}")
        print(f"- DEB (prefixo DEB:): {count_deb}")
        print(f"- Outros (sem prefixo): {count_outros}")
        print(f"- Ativos prioritários: {count_prioritarios}")
        print(f"- Total no portfólio: {len(todos_codigos)}")
        
        # Imprimir estatísticas por status
        print(f"Estatísticas por status:")
        for status, count in status_counts.items():
            print(f"- {status}: {count}")
        
        # Adicionar estatísticas ao retorno da API
        estatisticas = {
            "total_ativos_encontrados": len(ativos),
            "total_portfolio": len(todos_codigos),
            "ativos_prioritarios": count_prioritarios,
            "ativos_cri_cdca_cra": count_cri_cdca_cra,
            "ativos_deb": count_deb,
            "ativos_outros": count_outros,
            "status": status_counts
        }
        
        # Obter credenciais
        username = os.getenv("COMDINHEIRO_USERNAME")
        password = os.getenv("COMDINHEIRO_PASSWORD")
        
        if not username or not password:
            return jsonify({'error': 'Credenciais do Comdinheiro não encontradas nas variáveis de ambiente'}), 500
        
        # Dividir os ativos em grupos de no máximo 900
        MAX_ATIVOS_POR_PORTFOLIO = 900  # Reduzido para 900 para garantir que não ultrapasse o limite
        grupos_ativos = []
        
        # Processar todos os códigos de ativos
        total_ativos = len(todos_codigos)
        print(f"Total de ativos a serem distribuídos: {total_ativos}")
        
        # Dividir em grupos de no máximo 900 ativos
        for i in range(0, total_ativos, MAX_ATIVOS_POR_PORTFOLIO):
            fim = min(i + MAX_ATIVOS_POR_PORTFOLIO, total_ativos)
            grupo = todos_codigos[i:fim]
            grupos_ativos.append(grupo)
            print(f"Grupo {len(grupos_ativos)}: {len(grupo)} ativos (índices {i} até {fim-1})")
        
        # URL da API
        url = "https://api.comdinheiro.com.br/v1/ep1/export-data"
        
        # Lista para armazenar resultados de cada portfólio
        resultados_portfolios = []
        
        # Função para obter nova conexão
        def get_fresh_connection():
            try:
                conn = get_db_connection()
                if conn:
                    conn.autocommit = False  # Desabilitar autocommit para controle manual
                return conn
            except Exception as e:
                logging.error(f"Erro ao criar nova conexão: {str(e)}")
                return None
        
        # Processar cada portfólio
        for i, grupo in enumerate(grupos_ativos):
            try:
                logging.info(f"Processando portfólio: {grupo}")
                
                # Construir o payload específico para este portfólio
                payload = f"username={username}&password={password}&URL=HistoricoCotacao002.php%3F%26x%3DEXPLODE%28{grupo[0]}%29%26data_ini%3D{data_inicial.strftime('%d%m%Y')}%26data_fim%3D{data_final.strftime('%d%m%Y')}%26pagina%3D1%26d%3DMOEDA_ORIGINAL%26g%3D1%26m%3D0%26info_desejada%3Dpreco%26retorno%3Ddiscreto%26tipo_data%3Ddu_br%26tipo_ajuste%3Dtodosajustes%26num_casas%3D2%26enviar_email%3D0%26ordem_legenda%3D1%26cabecalho_excel%3Dmodo1%26classes_ativos%3Dz1ci99jj7473%26ordem_data%3D0%26rent_acum%3Drent_acum%26minY%3D%26maxY%3D%26deltaY%3D%26preco_nd_ant%3D1%26base_num_indice%3D100%26flag_num_indice%3D0%26eixo_x%3DData%26startX%3D0%26max_list_size%3D20%26line_width%3D2%26titulo_grafico%3D%26legenda_eixoy%3D%26tipo_grafico%3Dline%26script%3D%26tooltip%3Dunica&format=json3"
                
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                
                # Fazer a requisição para a API do Comdinheiro
                response = requests.post(url, data=payload, headers=headers)
                
                print(f"Status da resposta para {grupo}: {response.status_code}")
                print(f"Resposta: {response.text[:500]}...")
                
                if response.status_code == 401:
                    erro_msg = f"Erro de autenticação (401) na API do Comdinheiro para {grupo}. Verifique as credenciais."
                    erros.append(erro_msg)
                    logging.error(erro_msg)
                    continue
                elif response.status_code != 200:
                    erro_msg = f"Erro na API do Comdinheiro para {grupo}: {response.status_code} - {response.text}"
                    erros.append(erro_msg)
                    logging.error(erro_msg)
                    continue
                
                # Converter resposta para JSON
                dados_json = response.json()

                # Remover salvamento do JSON bruto para análise
                # (trecho removido)

                print(f"Processando dados JSON para portfólio: {grupo}")
                # Acessar a tabela correta
                tab1 = None
                if 'tables' in dados_json and 'tab1' in dados_json['tables']:
                    tab1 = dados_json['tables']['tab1']
                else:
                    erro_msg = f"Estrutura de dados não encontrada em {grupo}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                # Extrair cabeçalho de lin0
                if 'lin0' not in tab1:
                    erro_msg = f"Cabeçalho (lin0) não encontrado em {grupo}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                header = []
                col_map = {}
                for col_key, col_name in tab1['lin0'].items():
                    header.append(col_name)
                    col_map[col_key] = col_name

                # Verificação: se só tem 'Data' e o nome do portfólio, pular
                if len(header) <= 2 and any(grupo[0] in h for h in header):
                    erro_msg = f"Nenhum ativo válido encontrado no portfólio {grupo[0]} (header: {header})"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                # Extrair linhas de dados
                data_rows = []
                for lin_key in sorted(tab1.keys()):
                    if lin_key == 'lin0':
                        continue
                    row = []
                    for col_key in sorted(tab1[lin_key].keys(), key=lambda x: int(x.replace('col', ''))):
                        row.append(tab1[lin_key][col_key])
                    data_rows.append(row)

                # Criar DataFrame
                if not data_rows:
                    erro_msg = f"Nenhum dado encontrado em {grupo}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue


                df = pd.DataFrame(data_rows, columns=header)

                # Converter coluna Data para datetime
                if 'Data' in df.columns:
                    df['Data'] = pd.to_datetime(df['Data'], errors='coerce')

                # Processar cada coluna (exceto Data)
                for coluna in df.columns:
                    if coluna == 'Data':
                        continue
                    try:
                        dados_coluna = []
                        for idx, row in df.iterrows():
                            valor_data = row['Data']
                            valor_col = row[coluna]
                            if pd.notna(valor_col) and pd.notna(valor_data):
                                try:
                                    valor = float(str(valor_col).replace(',', '.'))
                                except Exception:
                                    valor = None
                                dados_coluna.append({
                                    'data': valor_data.strftime('%d/%m/%Y') if hasattr(valor_data, 'strftime') else str(valor_data),
                                    'valor': valor
                                })
                        if dados_coluna:
                            json_final = {
                                'ativo': coluna,
                                'portfolio_origem': grupo[0],
                                'total_registros': len(dados_coluna),
                                'primeira_data': dados_coluna[0]['data'],
                                'ultima_data': dados_coluna[-1]['data'],
                                'dados': dados_coluna
                            }
                            dados_json_string = json.dumps(json_final, ensure_ascii=False)
                            conn = get_fresh_connection()
                            if conn:
                                cursor = conn.cursor()
                                try:
                                    cursor.execute("SELECT id FROM dados_cmd WHERE ativo = %s", (coluna,))
                                    ativo_existente = cursor.fetchone()
                                    if ativo_existente:
                                        cursor.execute(
                                            "UPDATE dados_cmd SET dados = %s, data_insercao = NOW() WHERE ativo = %s",
                                            (dados_json_string, coluna)
                                        )
                                        print(f"  ✅ Atualizado: {coluna}")
                                    else:
                                        cursor.execute(
                                            "INSERT INTO dados_cmd (ativo, dados) VALUES (%s, %s)",
                                            (coluna, dados_json_string)
                                        )
                                        print(f"  ✅ Inserido: {coluna}")
                                    conn.commit()
                                    ativos_processados += 1
                                except Exception as e:
                                    conn.rollback()
                                    erro_msg = f"Erro ao salvar {coluna} no BD: {str(e)}"
                                    erros.append(erro_msg)
                                    logging.error(erro_msg)
                                finally:
                                    cursor.close()
                                    conn.close()
                            else:
                                erro_msg = f"Não foi possível conectar ao BD para salvar {coluna}"
                                erros.append(erro_msg)
                    except Exception as e:
                        erro_msg = f"Erro ao processar coluna {coluna} do portfólio {grupo}: {str(e)}"
                        erros.append(erro_msg)
                        logging.error(erro_msg)
                
                portfolios_processados.append(grupo)
                logging.info(f"Portfólio {grupo} processado com sucesso")
                
            except Exception as e:
                erro_msg = f"Erro ao processar portfólio {grupo}: {str(e)}"
                erros.append(erro_msg)
                logging.error(erro_msg)
        
        return jsonify({
            'message': f'Dados atualizados com sucesso! {ativos_processados} ativos processados de {len(portfolios_processados)} portfólios.',
            'ativos_processados': ativos_processados,
            'portfolios_processados': portfolios_processados,
            'erros': erros,
            'data_inicial': data_inicial.strftime("%d/%m/%Y"),
            'data_final': data_final.strftime("%d/%m/%Y")
        }), 200
            
    except Error as e:
        return jsonify({'error': f'Erro de banco de dados: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao gerar portfólio: {str(e)}'}), 500

@portfolio_bp.route('/api/ativos-prioritarios', methods=['GET'])
@token_required
def listar_ativos_prioritarios(current_user=None):
    """
    Lista todos os ativos prioritários cadastrados
    
    Retorno:
    - JSON com a lista de ativos prioritários ordenados
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerenciar ativos prioritários'}), 403
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Buscar ativos prioritários
        cursor.execute("""
            SELECT id, codigo, descricao, ordem
            FROM ativos_prioritarios
            ORDER BY ordem
        """)
        
        ativos = cursor.fetchall()
        
        cursor.close()
        connection.close()
        
        return jsonify({'ativos_prioritarios': ativos}), 200
        
    except Error as e:
        return jsonify({'error': f'Erro ao listar ativos prioritários: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao processar requisição: {str(e)}'}), 500

@portfolio_bp.route('/api/ativos-prioritarios', methods=['POST'])
@token_required
def adicionar_ativo_prioritario(current_user=None):
    """
    Adiciona um novo ativo prioritário
    
    Parâmetros (JSON):
    - codigo: Código do ativo (obrigatório)
    - descricao: Descrição do ativo (opcional)
    - ordem: Ordem de prioridade (opcional, padrão: último)
    
    Retorno:
    - JSON com informações sobre o ativo adicionado
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerenciar ativos prioritários'}), 403
        
        data = request.get_json()
        if not data or 'codigo' not in data:
            return jsonify({'error': 'Código do ativo é obrigatório'}), 400
            
        codigo = data['codigo']
        descricao = data.get('descricao', '')
        ordem = data.get('ordem')
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor()
        
        # Se não foi especificada ordem, pegar a próxima disponível
        if ordem is None:
            cursor.execute("SELECT COALESCE(MAX(ordem), 0) + 1 FROM ativos_prioritarios")
            ordem = cursor.fetchone()[0]
        
        # Inserir o ativo prioritário
        cursor.execute("""
            INSERT INTO ativos_prioritarios (codigo, descricao, ordem)
            VALUES (%s, %s, %s)
        """, (codigo, descricao, ordem))
        
        ativo_id = cursor.lastrowid
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'message': 'Ativo prioritário adicionado com sucesso',
            'id': ativo_id,
            'codigo': codigo,
            'descricao': descricao,
            'ordem': ordem
        }), 201
        
    except Error as e:
        if 'Duplicate entry' in str(e):
            return jsonify({'error': 'Este código já está cadastrado como ativo prioritário'}), 400
        return jsonify({'error': f'Erro ao adicionar ativo prioritário: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao processar requisição: {str(e)}'}), 500

@portfolio_bp.route('/api/ativos-prioritarios/<int:ativo_id>', methods=['PUT'])
@token_required
def atualizar_ativo_prioritario(ativo_id, current_user=None):
    """
    Atualiza um ativo prioritário existente
    
    Parâmetros (JSON):
    - codigo: Código do ativo (opcional)
    - descricao: Descrição do ativo (opcional)
    - ordem: Ordem de prioridade (opcional)
    
    Retorno:
    - JSON com informações sobre o ativo atualizado
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerenciar ativos prioritários'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Nenhum dado fornecido para atualização'}), 400
            
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Verificar se o ativo existe
        cursor.execute("SELECT * FROM ativos_prioritarios WHERE id = %s", (ativo_id,))
        ativo = cursor.fetchone()
        
        if not ativo:
            cursor.close()
            connection.close()
            return jsonify({'error': 'Ativo prioritário não encontrado'}), 404
        
        # Preparar dados para atualização
        codigo = data.get('codigo', ativo['codigo'])
        descricao = data.get('descricao', ativo['descricao'])
        ordem = data.get('ordem', ativo['ordem'])
        
        # Atualizar o ativo
        cursor.execute("""
            UPDATE ativos_prioritarios
            SET codigo = %s, descricao = %s, ordem = %s
            WHERE id = %s
        """, (codigo, descricao, ordem, ativo_id))
        
        connection.commit()
        
        # Buscar o ativo atualizado
        cursor.execute("SELECT * FROM ativos_prioritarios WHERE id = %s", (ativo_id,))
        ativo_atualizado = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'message': 'Ativo prioritário atualizado com sucesso',
            'ativo': ativo_atualizado
        }), 200
        
    except Error as e:
        if 'Duplicate entry' in str(e):
            return jsonify({'error': 'Este código já está cadastrado como ativo prioritário'}), 400
        return jsonify({'error': f'Erro ao atualizar ativo prioritário: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao processar requisição: {str(e)}'}), 500

@portfolio_bp.route('/api/ativos-prioritarios/<int:ativo_id>', methods=['DELETE'])
@token_required
def excluir_ativo_prioritario(ativo_id, current_user=None):
    """
    Exclui um ativo prioritário
    
    Retorno:
    - JSON com mensagem de confirmação
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerenciar ativos prioritários'}), 403
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor()
        
        # Verificar se o ativo existe
        cursor.execute("SELECT COUNT(*) FROM ativos_prioritarios WHERE id = %s", (ativo_id,))
        if cursor.fetchone()[0] == 0:
            cursor.close()
            connection.close()
            return jsonify({'error': 'Ativo prioritário não encontrado'}), 404
        
        # Excluir o ativo
        cursor.execute("DELETE FROM ativos_prioritarios WHERE id = %s", (ativo_id,))
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'message': 'Ativo prioritário excluído com sucesso'
        }), 200
        
    except Error as e:
        return jsonify({'error': f'Erro ao excluir ativo prioritário: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao processar requisição: {str(e)}'}), 500

@portfolio_bp.route('/api/ativos-prioritarios/reordenar', methods=['PUT'])
@token_required
def reordenar_ativos_prioritarios(current_user=None):
    """
    Reordena a lista de ativos prioritários
    
    Parâmetros (JSON):
    - ativos: Lista de objetos com id e ordem
    
    Retorno:
    - JSON com mensagem de confirmação
    """
    try:
        # Verificar se o usuário tem permissão
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem gerenciar ativos prioritários'}), 403
        
        data = request.get_json()
        if not data or 'ativos' not in data:
            return jsonify({'error': 'Lista de ativos é obrigatória'}), 400
            
        ativos = data['ativos']
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
            
        cursor = connection.cursor()
        
        # Atualizar a ordem de cada ativo
        for ativo in ativos:
            if 'id' not in ativo or 'ordem' not in ativo:
                continue
                
            cursor.execute("""
                UPDATE ativos_prioritarios
                SET ordem = %s
                WHERE id = %s
            """, (ativo['ordem'], ativo['id']))
        
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'message': 'Ordem dos ativos prioritários atualizada com sucesso'
        }), 200
        
    except Error as e:
        return jsonify({'error': f'Erro ao reordenar ativos prioritários: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Erro ao processar requisição: {str(e)}'}), 500 

@portfolio_bp.route('/atualizar-dados', methods=['POST'])
@token_required
def atualizar_dados_portfolio(current_user):
    try:
        print("=== INICIANDO ATUALIZAÇÃO DE DADOS ===")
        
        # Configurações da API do Comdinheiro
        url = "https://api.comdinheiro.com.br/v1/ep1/import-data"
        
        # Buscar credenciais do arquivo .env
        username = os.getenv('COMDINHEIRO_USERNAME')
        password = os.getenv('COMDINHEIRO_PASSWORD')
        
        print(f"Username configurado: {'Sim' if username else 'Não'}")
        print(f"Password configurado: {'Sim' if password else 'Não'}")
        
        if not username or not password:
            return jsonify({'error': 'Credenciais do Comdinheiro não configuradas no arquivo .env'}), 500
        
        # Calcular datas (15 anos atrás até hoje)
        data_final = datetime.now()
        data_inicial = data_final - timedelta(days=15*365)  # Aproximadamente 15 anos
        
        # Formatar datas no formato esperado pela API (DDMMYYYY)
        data_ini = data_inicial.strftime("%d%m%Y")
        data_fim = data_final.strftime("%d%m%Y")
        
        print(f"Período de busca: {data_ini} a {data_fim}")
        
        # Lista de possíveis portfólios para testar
        possiveis_portfolios = ['ativos_Research']
        for i in range(1, 11):  # ativos_Research1 até ativos_Research10
            possiveis_portfolios.append(f'ativos_Research{i}')
        
        print(f"Portfólios a testar: {possiveis_portfolios}")
        
        portfolios_validos = []
        
        # Testar cada portfólio para ver se existe na API do Comdinheiro
        for portfolio in possiveis_portfolios:
            try:
                print(f"\n--- Testando portfólio: {portfolio} ---")
                
                # Fazer uma requisição de teste para verificar se o portfólio existe
                payload_teste = f"username={username}&password={password}&URL=HistoricoCotacao002.php%3F%26x%3DEXPLODE%28{portfolio}%29%26data_ini%3D{data_ini}%26data_fim%3D{data_fim}%26pagina%3D1%26d%3DMOEDA_ORIGINAL%26g%3D1%26m%3D0%26info_desejada%3Dpreco%26retorno%3Ddiscreto%26tipo_data%3Ddu_br%26tipo_ajuste%3Dtodosajustes%26num_casas%3D2%26enviar_email%3D0%26ordem_legenda%3D1%26cabecalho_excel%3Dmodo1%26classes_ativos%3Dz1ci99jj7473%26ordem_data%3D0%26rent_acum%3Drent_acum%26minY%3D%26maxY%3D%26deltaY%3D%26preco_nd_ant%3D1%26base_num_indice%3D100%26flag_num_indice%3D0%26eixo_x%3DData%26startX%3D0%26max_list_size%3D20%26line_width%3D2%26titulo_grafico%3D%26legenda_eixoy%3D%26tipo_grafico%3Dline%26script%3D%26tooltip%3Dunica&format=json3"
                
                print(f"Payload de teste para {portfolio}:")
                print(f"URL: {url}")
                print(f"Payload: {payload_teste[:200]}...")  # Mostrar apenas os primeiros 200 caracteres
                
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                response_teste = requests.post(url, data=payload_teste, headers=headers)
                
                print(f"Status da resposta: {response_teste.status_code}")
                
                print(f"Resposta completa: {response_teste.text}")
                
                if response_teste.status_code == 200:
                    try:
                        dados_teste = response_teste.json()
                        print(f"Dados retornados: {len(dados_teste) if dados_teste else 0} itens")
                        
                        # Se retornou dados válidos (não vazio), o portfólio existe
                        if dados_teste and len(dados_teste) > 0:
                            portfolios_validos.append(portfolio)
                            print(f"✅ Portfólio {portfolio} encontrado e será processado")
                        else:
                            print(f"❌ Portfólio {portfolio} não retornou dados válidos")
                    except Exception as json_error:
                        print(f"❌ Erro ao processar JSON da resposta: {str(json_error)}")
                        print(f"Resposta bruta: {response_teste.text}")
                elif response_teste.status_code == 401:
                    print(f"❌ Erro de autenticação (401) para {portfolio}")
                    print(f"Verifique se as credenciais estão corretas")
                    print(f"Resposta: {response_teste.text}")
                else:
                    print(f"❌ Portfólio {portfolio} não encontrado (status: {response_teste.status_code})")
                    print(f"Resposta: {response_teste.text[:200]}...")
                    
            except Exception as e:
                print(f"⚠️ Erro ao testar portfólio {portfolio}: {str(e)}")
                continue
        
        print(f"\n=== RESUMO DOS PORTFÓLIOS ===")
        print(f"Portfólios válidos encontrados: {portfolios_validos}")
        
        if not portfolios_validos:
            return jsonify({'error': 'Nenhum portfólio válido encontrado para atualizar'}), 404
        
        logging.info(f"Portfólios válidos encontrados: {portfolios_validos}")
        
        ativos_processados = 0
        erros = []
        portfolios_processados = []
        
        # Função para obter nova conexão
        def get_fresh_connection():
            try:
                conn = get_db_connection()
                if conn:
                    conn.autocommit = False  # Desabilitar autocommit para controle manual
                return conn
            except Exception as e:
                logging.error(f"Erro ao criar nova conexão: {str(e)}")
                return None
        
        # Processar cada portfólio
        for portfolio in portfolios_validos:
            try:
                logging.info(f"Processando portfólio: {portfolio}")
                
                # Construir o payload específico para este portfólio
                payload = f"username={username}&password={password}&URL=HistoricoCotacao002.php%3F%26x%3DEXPLODE%28{portfolio}%29%26data_ini%3D{data_ini}%26data_fim%3D{data_fim}%26pagina%3D1%26d%3DMOEDA_ORIGINAL%26g%3D1%26m%3D0%26info_desejada%3Dpreco%26retorno%3Ddiscreto%26tipo_data%3Ddu_br%26tipo_ajuste%3Dtodosajustes%26num_casas%3D2%26enviar_email%3D0%26ordem_legenda%3D1%26cabecalho_excel%3Dmodo1%26classes_ativos%3Dz1ci99jj7473%26ordem_data%3D0%26rent_acum%3Drent_acum%26minY%3D%26maxY%3D%26deltaY%3D%26preco_nd_ant%3D1%26base_num_indice%3D100%26flag_num_indice%3D0%26eixo_x%3DData%26startX%3D0%26max_list_size%3D20%26line_width%3D2%26titulo_grafico%3D%26legenda_eixoy%3D%26tipo_grafico%3Dline%26script%3D%26tooltip%3Dunica&format=json3"
                
                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                
                # Fazer a requisição para a API do Comdinheiro
                response = requests.post(url, data=payload, headers=headers)
                
                print(f"Status da resposta para {portfolio}: {response.status_code}")
                print(f"Resposta: {response.text[:500]}...")
                
                if response.status_code == 401:
                    erro_msg = f"Erro de autenticação (401) na API do Comdinheiro para {portfolio}. Verifique as credenciais."
                    erros.append(erro_msg)
                    logging.error(erro_msg)
                    continue
                elif response.status_code != 200:
                    erro_msg = f"Erro na API do Comdinheiro para {portfolio}: {response.status_code} - {response.text}"
                    erros.append(erro_msg)
                    logging.error(erro_msg)
                    continue
                
                # Converter resposta para JSON
                dados_json = response.json()

                # Remover salvamento do JSON bruto para análise
                # (trecho removido)

                print(f"Processando dados JSON para portfólio: {portfolio}")
                # Acessar a tabela correta
                tab1 = None
                if 'tables' in dados_json and 'tab1' in dados_json['tables']:
                    tab1 = dados_json['tables']['tab1']
                else:
                    erro_msg = f"Estrutura de dados não encontrada em {portfolio}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                # Extrair cabeçalho de lin0
                if 'lin0' not in tab1:
                    erro_msg = f"Cabeçalho (lin0) não encontrado em {portfolio}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                header = []
                col_map = {}
                for col_key, col_name in tab1['lin0'].items():
                    header.append(col_name)
                    col_map[col_key] = col_name

                # Verificação: se só tem 'Data' e o nome do portfólio, pular
                if len(header) <= 2 and any(portfolio in h for h in header):
                    erro_msg = f"Nenhum ativo válido encontrado no portfólio {portfolio} (header: {header})"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue

                # Extrair linhas de dados
                data_rows = []
                for lin_key in sorted(tab1.keys()):
                    if lin_key == 'lin0':
                        continue
                    row = []
                    for col_key in sorted(tab1[lin_key].keys(), key=lambda x: int(x.replace('col', ''))):
                        row.append(tab1[lin_key][col_key])
                    data_rows.append(row)

                # Criar DataFrame
                if not data_rows:
                    erro_msg = f"Nenhum dado encontrado em {portfolio}"
                    erros.append(erro_msg)
                    print(f"❌ {erro_msg}")
                    continue


                df = pd.DataFrame(data_rows, columns=header)

                # Converter coluna Data para datetime
                if 'Data' in df.columns:
                    df['Data'] = pd.to_datetime(df['Data'], errors='coerce')

                # Processar cada coluna (exceto Data)
                for coluna in df.columns:
                    if coluna == 'Data':
                        continue
                    try:
                        dados_coluna = []
                        for idx, row in df.iterrows():
                            valor_data = row['Data']
                            valor_col = row[coluna]
                            if pd.notna(valor_col) and pd.notna(valor_data):
                                try:
                                    valor = float(str(valor_col).replace(',', '.'))
                                except Exception:
                                    valor = None
                                dados_coluna.append({
                                    'data': valor_data.strftime('%d/%m/%Y') if hasattr(valor_data, 'strftime') else str(valor_data),
                                    'valor': valor
                                })
                        if dados_coluna:
                            json_final = {
                                'ativo': coluna,
                                'portfolio_origem': portfolio,
                                'total_registros': len(dados_coluna),
                                'primeira_data': dados_coluna[0]['data'],
                                'ultima_data': dados_coluna[-1]['data'],
                                'dados': dados_coluna
                            }
                            dados_json_string = json.dumps(json_final, ensure_ascii=False)
                            conn = get_fresh_connection()
                            if conn:
                                cursor = conn.cursor()
                                try:
                                    cursor.execute("SELECT id FROM dados_cmd WHERE ativo = %s", (coluna,))
                                    ativo_existente = cursor.fetchone()
                                    if ativo_existente:
                                        cursor.execute(
                                            "UPDATE dados_cmd SET dados = %s, data_insercao = NOW() WHERE ativo = %s",
                                            (dados_json_string, coluna)
                                        )
                                        print(f"  ✅ Atualizado: {coluna}")
                                    else:
                                        cursor.execute(
                                            "INSERT INTO dados_cmd (ativo, dados) VALUES (%s, %s)",
                                            (coluna, dados_json_string)
                                        )
                                        print(f"  ✅ Inserido: {coluna}")
                                    conn.commit()
                                    ativos_processados += 1
                                except Exception as e:
                                    conn.rollback()
                                    erro_msg = f"Erro ao salvar {coluna} no BD: {str(e)}"
                                    erros.append(erro_msg)
                                    logging.error(erro_msg)
                                finally:
                                    cursor.close()
                                    conn.close()
                            else:
                                erro_msg = f"Não foi possível conectar ao BD para salvar {coluna}"
                                erros.append(erro_msg)
                    except Exception as e:
                        erro_msg = f"Erro ao processar coluna {coluna} do portfólio {portfolio}: {str(e)}"
                        erros.append(erro_msg)
                        logging.error(erro_msg)
                
                portfolios_processados.append(portfolio)
                logging.info(f"Portfólio {portfolio} processado com sucesso")
                
            except Exception as e:
                erro_msg = f"Erro ao processar portfólio {portfolio}: {str(e)}"
                erros.append(erro_msg)
                logging.error(erro_msg)
        
        return jsonify({
            'message': f'Dados atualizados com sucesso! {ativos_processados} ativos processados de {len(portfolios_processados)} portfólios.',
            'ativos_processados': ativos_processados,
            'portfolios_processados': portfolios_processados,
            'erros': erros,
            'data_inicial': data_inicial.strftime("%d/%m/%Y"),
            'data_final': data_final.strftime("%d/%m/%Y")
        }), 200
            
    except Exception as e:
        logging.error(f"Erro ao atualizar dados: {str(e)}")
        return jsonify({'error': f'Erro interno do servidor: {str(e)}'}), 500 

@portfolio_bp.route('/api/dados-cmd/ativos', methods=['GET'])
@token_required
def listar_ativos_dados_cmd(current_user=None):
    """
    Retorna a lista de ativos disponíveis na tabela dados_cmd
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT ativo FROM dados_cmd ORDER BY ativo")
        ativos = [row['ativo'] for row in cursor.fetchall()]
        cursor.close()
        connection.close()
        return jsonify({'ativos': ativos}), 200
    except Exception as e:
        return jsonify({'error': f'Erro ao buscar ativos: {str(e)}'}), 500

@portfolio_bp.route('/api/dados-cmd/series', methods=['POST'])
@token_required
def buscar_series_historicas(current_user=None):
    """
    Recebe uma lista de ativos e retorna as séries históricas (datas e valores) de cada um
    Parâmetros: { "ativos": ["PETR4", "VALE3", ...] }
    """
    try:
        data = request.get_json()
        ativos = data.get('ativos', [])
        if not ativos or not isinstance(ativos, list):
            return jsonify({'error': 'Lista de ativos é obrigatória'}), 400
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
        cursor = connection.cursor(dictionary=True)
        series = {}
        for ativo in ativos:
            cursor.execute("SELECT dados FROM dados_cmd WHERE ativo = %s", (ativo,))
            row = cursor.fetchone()
            if not row:
                continue
            dados_json = json.loads(row['dados'])
            # Cada item: { 'data': 'dd/mm/yyyy', 'valor': float }
            serie = [
                {'data': item['data'], 'valor': item['valor']} 
                for item in dados_json.get('dados', []) if item.get('valor') is not None
            ]
            series[ativo] = serie
        cursor.close()
        connection.close()
        return jsonify({'series': series}), 200
    except Exception as e:
        return jsonify({'error': f'Erro ao buscar séries históricas: {str(e)}'}), 500

@portfolio_bp.route('/api/dados-cmd/correlacao-covariancia', methods=['POST'])
@token_required
def correlacao_covariancia_ativos(current_user=None):
    """
    Recebe uma lista de ativos e retorna as matrizes de correlação e covariância entre eles
    Parâmetros: { "ativos": ["PETR4", "VALE3", ...] }
    """
    try:
        data = request.get_json()
        ativos = data.get('ativos', [])
        if not ativos or not isinstance(ativos, list) or len(ativos) < 2:
            return jsonify({'error': 'Selecione pelo menos dois ativos'}), 400
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500
        cursor = connection.cursor(dictionary=True)
        # Montar DataFrame com datas como índice

        df_dict = {}
        for ativo in ativos:
            cursor.execute("SELECT dados FROM dados_cmd WHERE ativo = %s", (ativo,))
            row = cursor.fetchone()
            if not row:
                continue
            dados_json = json.loads(row['dados'])
            serie = [
                {'data': item['data'], 'valor': item['valor']} 
                for item in dados_json.get('dados', []) if item.get('valor') is not None
            ]
            # Ordenar por data para garantir a sequência correta
            serie = sorted(serie, key=lambda x: pd.to_datetime(x['data'], dayfirst=True))
            s = pd.Series(
                data=[item['valor'] for item in serie],
                index=[pd.to_datetime(item['data'], dayfirst=True) for item in serie],
                name=ativo
            )
            df_dict[ativo] = s
        cursor.close()
        connection.close()
        if not df_dict:
            return jsonify({'error': 'Nenhum dado encontrado para os ativos selecionados'}), 404
            
        # Concatenar séries em um DataFrame e ordenar por data
        df = pd.concat(df_dict.values(), axis=1, join='inner').sort_index()
        
        # Calcular retornos diários (variação percentual)
        df_ret = pd.DataFrame(index=df.index)
        
        # Tratar cada ativo individualmente
        for coluna in df.columns:
            if coluna == 'CDI':
                # Para o CDI, converter taxa anual para taxa diária
                # Taxa diária = (1 + taxa anual/100)^(1/252) - 1
                taxa_diaria = ((1 + df[coluna]/100)**(1/252) - 1)
                # Calcular retorno como diferença entre taxas diárias
                df_ret[coluna] = taxa_diaria.diff().fillna(0)
            else:
                # Para outros ativos, calcular retorno logarítmico: ln(Pt/Pt-1)
                # Isso é mais preciso para cálculos financeiros e estatísticos
                df_ret[coluna] = np.log(df[coluna] / df[coluna].shift(1)).fillna(0)
        
        # Remover a primeira linha que terá NaN
        df_ret = df_ret.iloc[1:].copy()
        
        # Calcular correlação e covariância dos retornos
        correl = df_ret.corr().round(4).to_dict()
        covar = df_ret.cov().round(4).to_dict()
        
        # Calcular retorno médio anualizado e volatilidade anualizada
        # Multiplicamos por 252 (dias úteis em um ano) para anualizar
        retorno = (df_ret.mean() * 252).round(4).to_dict()
        
        # Volatilidade anualizada = desvio padrão diário * raiz quadrada de 252
        risco = (df_ret.std() * np.sqrt(252)).round(4).to_dict()
        
        return jsonify({
            'correlacao': correl,
            'covariancia': covar,
            'retorno': retorno,
            'risco': risco
        }), 200
    except Exception as e:
        print(f"Erro ao calcular correlação/covariância: {str(e)}")

        traceback.print_exc()
        return jsonify({'error': f'Erro ao calcular correlação/covariância: {str(e)}'}), 500

@portfolio_bp.route('/api/markowitz/eficiente', methods=['POST'])
@token_required
def calcular_carteira_eficiente(current_user=None):
    """
    Calcula a carteira eficiente de Markowitz com base nos ativos selecionados
    
    Parâmetros:
    - ativos: Lista de códigos dos ativos
    - series: Dicionário com séries históricas de cada ativo
    - dataInicial: Data inicial para filtrar (opcional)
    - dataFinal: Data final para filtrar (opcional)
    
    Retorna:
    - resultado: Informações sobre a carteira eficiente (alocação, retorno, risco, sharpe)
    - backtest: Dados para backtest da carteira
    """
    try:
        data = request.get_json()
        ativos = data.get('ativos', [])
        series_input = data.get('series', {})
        data_inicial = data.get('dataInicial')
        data_final = data.get('dataFinal')
        
        if not ativos or len(ativos) < 2:
            return jsonify({'error': 'Selecione pelo menos dois ativos'}), 400
        
        if not series_input:
            return jsonify({'error': 'Séries históricas não fornecidas'}), 400
        

        
        
        # Converter séries para DataFrame
        df_dict = {}
        for ativo in ativos:
            if ativo not in series_input:
                continue
                
            serie = series_input[ativo]
            if not serie:
                continue
                
            # Ordenar por data para garantir a sequência correta
            serie = sorted(serie, key=lambda x: pd.to_datetime('/'.join(x['data'].split('/')[::-1])))
            
            # Converter para Series do pandas
            s = pd.Series(
                data=[item['valor'] for item in serie if item.get('valor') is not None],
                index=[pd.to_datetime('/'.join(item['data'].split('/')[::-1])) for item in serie if item.get('valor') is not None],
                name=ativo
            )
            df_dict[ativo] = s
            
        if not df_dict:
            return jsonify({'error': 'Nenhum dado válido encontrado para os ativos selecionados'}), 404
            
        # Concatenar séries em um DataFrame
        df = pd.concat(df_dict.values(), axis=1, join='inner').sort_index()
        
        # Filtrar por data se necessário
        if data_inicial:
            df = df[df.index >= pd.to_datetime(data_inicial)]
        if data_final:
            df = df[df.index <= pd.to_datetime(data_final)]
            
        if len(df) < 20:  # Verificar se há dados suficientes
            return jsonify({'error': 'Dados insuficientes para análise. Selecione um período maior.'}), 400
        
        # Calcular retornos diários
        df_ret = pd.DataFrame(index=df.index)
        
        # Tratar cada ativo individualmente
        for coluna in df.columns:
            if coluna == 'CDI':
                # Para o CDI, converter taxa anual para taxa diária
                # Taxa diária = (1 + taxa anual/100)^(1/252) - 1
                taxa_diaria = ((1 + df[coluna]/100)**(1/252) - 1)
                # Calcular retorno como diferença entre taxas diárias
                df_ret[coluna] = taxa_diaria.diff().fillna(0)
            else:
                # Para outros ativos, calcular retorno logarítmico: ln(Pt/Pt-1)
                # Isso é mais preciso para cálculos financeiros e estatísticos
                df_ret[coluna] = np.log(df[coluna] / df[coluna].shift(1)).fillna(0)
        
        # Remover a primeira linha que terá NaN
        df_ret = df_ret.iloc[1:].copy()
        
        # Calcular estatísticas
        retornos_medios = pd.Series(index=df_ret.columns)
        
        # Tratar retornos médios de forma especial para o CDI
        for coluna in df_ret.columns:
            if coluna == 'CDI':
                # Para CDI, usar a média das taxas anuais diretamente
                # ao invés de calcular com base nos retornos diários
                retornos_medios[coluna] = df[coluna].mean() / 100  # Converter % para decimal
            else:
                # Para outros ativos, calcular normalmente
                retornos_medios[coluna] = df_ret[coluna].mean() * 252
        
        # Anualizar a matriz de covariância multiplicando por 252 dias úteis
        matriz_cov = df_ret.cov() * 252
        
        # Função para calcular retorno e volatilidade do portfólio
        def portfolio_stats(weights, returns, cov_matrix):
            portfolio_return = np.sum(returns * weights) 
            # Calcular volatilidade com verificação para evitar valores negativos devido a erros numéricos
            portfolio_variance = np.dot(weights.T, np.dot(cov_matrix, weights))
            # Garantir que a variância seja positiva
            if portfolio_variance < 0:
                portfolio_variance = 0.0001
            portfolio_volatility = np.sqrt(portfolio_variance)
            
            # Garantir um valor mínimo de volatilidade para evitar problemas numéricos
            if portfolio_volatility < 0.0001:
                portfolio_volatility = 0.0001
                
            return portfolio_return, portfolio_volatility
        
        # Calcular a taxa livre de risco com base no CDI
        taxa_livre_risco = 0.10  # Valor padrão caso não encontre CDI
        if 'CDI' in df.columns:
            taxa_livre_risco = df['CDI'].mean() / 100
        
        # Função objetivo para minimizar (negativo do Sharpe Ratio)
        def neg_sharpe_ratio(weights, returns, cov_matrix, risk_free_rate=None):
            # Usar a taxa livre de risco calculada a partir do CDI
            if risk_free_rate is None:
                risk_free_rate = taxa_livre_risco
                
            p_ret, p_vol = portfolio_stats(weights, returns, cov_matrix)
            # Evitar divisão por valores muito pequenos
            if p_vol < 0.0001:
                p_vol = 0.0001
            return -(p_ret - risk_free_rate) / p_vol
        
        # Restrições: soma dos pesos = 1 e pesos >= 0
        constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
        bounds = tuple((0, 1) for _ in range(len(ativos)))
        
        # Pesos iniciais iguais
        initial_weights = np.array([1/len(ativos)] * len(ativos))
        
        # Otimização
        result = minimize(
            neg_sharpe_ratio,
            initial_weights,
            args=(retornos_medios, matriz_cov),
            method='SLSQP',
            bounds=bounds,
            constraints=constraints
        )
        
        # Extrair resultados
        optimal_weights = result['x']
        
        # Calcular estatísticas da carteira otimizada
        portfolio_return, portfolio_volatility = portfolio_stats(optimal_weights, retornos_medios, matriz_cov)
        
        # Usar a taxa livre de risco já calculada anteriormente (definida antes da função neg_sharpe_ratio)
        
        # Verificar se a volatilidade não é muito próxima de zero para evitar divisão por valores muito pequenos
        if portfolio_volatility < 0.0001:  # Limitar volatilidade mínima para evitar Sharpe extremo
            portfolio_volatility = 0.0001
            
        sharpe_ratio = (portfolio_return - taxa_livre_risco) / portfolio_volatility
        
        # Preparar resultados
        alocacao = {}
        for i, ativo in enumerate(df_ret.columns):
            alocacao[ativo] = float(optimal_weights[i])
        
        # Calcular série histórica da carteira para backtest
        df_ret_carteira = pd.DataFrame(index=df_ret.index)
        df_ret_carteira['retorno_diario'] = 0
        
        # Para backtest mais preciso, tratar CDI de forma especial
        for i, ativo in enumerate(df_ret.columns):
            peso = optimal_weights[i]
            if ativo == 'CDI' and peso > 0:
                # Para CDI, usar a taxa diária real
                # Taxa diária = (1 + taxa anual/100)^(1/252) - 1
                taxa_diaria = ((1 + df.loc[df_ret.index, ativo]/100)**(1/252) - 1)
                df_ret_carteira['retorno_diario'] += taxa_diaria * peso
            else:
                # Para outros ativos, usar retornos calculados
                df_ret_carteira['retorno_diario'] += df_ret[ativo] * peso
        
        # Calcular retorno acumulado (base 100)
        df_ret_carteira['retorno_acumulado'] = (1 + df_ret_carteira['retorno_diario']).cumprod() * 100 - 100
        
        # Converter para formato de série para retornar
        backtest_series = []
        for idx, row in df_ret_carteira.iterrows():
            backtest_series.append({
                'data': idx.strftime('%d/%m/%Y'),
                'valor': float(row['retorno_acumulado'])
            })
        
        # Calcular retorno total no período
        retorno_total = float(df_ret_carteira['retorno_acumulado'].iloc[-1])
        
        # Gerar parecer sobre a carteira
        parecer = f"Carteira otimizada com base no modelo de Markowitz. "
        parecer += f"Retorno esperado anual de {(portfolio_return*100):.2f}% "
        parecer += f"com volatilidade de {(portfolio_volatility*100):.2f}%. "
        
        # Limitar o Sharpe para valores mais realistas na exibição
        sharpe_exibir = min(sharpe_ratio, 10.0)  # Limitar a 10 para exibição
        parecer += f"Índice de Sharpe: {sharpe_exibir:.2f} (considerando taxa livre de risco de {taxa_livre_risco*100:.2f}% a.a. baseada na média do CDI)."
        
        # Adicionar informação sobre o CDI se estiver presente na carteira
        if 'CDI' in alocacao and alocacao['CDI'] > 0.5:  # Se CDI for mais de 50% da carteira
            cdi_medio = df['CDI'].mean() if 'CDI' in df.columns else 0
            parecer += f" A carteira é composta principalmente por CDI ({alocacao['CDI']*100:.2f}%), "
            parecer += f"com taxa média anual de {cdi_medio:.2f}%."
        
        return jsonify({
            'resultado': {
                'alocacao': alocacao,
                'retorno': float(portfolio_return),
                'risco': float(portfolio_volatility),
                'sharpe': float(sharpe_ratio),
                'parecer': parecer
            },
            'backtest': {
                'series': backtest_series,
                'retornoTotal': retorno_total
            }
        }), 200
        
    except Exception as e:
        print(f"Erro ao calcular carteira eficiente: {str(e)}")

        traceback.print_exc()
        return jsonify({'error': f'Erro ao calcular carteira eficiente: {str(e)}'}), 500