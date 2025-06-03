from flask import Blueprint, jsonify, request
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error
import json
from datetime import datetime

statistics_bp = Blueprint('statistics', __name__)

@statistics_bp.route('/api/estatisticas/usuarios', methods=['GET'])
@token_required
def get_estatisticas_usuarios(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin ou Alocacao
        if request.user_role not in ['Admin', 'Alocacao']:
            return jsonify({'error': 'Acesso negado: somente Admins e Alocacao podem ver estatísticas'}), 403

        # Query para buscar usuários e quantidade de clientes
        query = """
            SELECT 
                u.id as id_usuario,
                u.username as nome_usuario,
                COUNT(c.id) as quantidade_clientes
            FROM user u
            LEFT JOIN client c ON u.id = c.user_id
            GROUP BY u.id, u.username
            ORDER BY u.username
        """
        
        cursor.execute(query)
        estatisticas = cursor.fetchall()

        return jsonify({'estatisticas': estatisticas}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar estatísticas de usuários: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@statistics_bp.route('/api/estatisticas/perfil_risco', methods=['GET'])
@token_required
def get_estatisticas_perfil_risco(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin ou Alocacao
        if request.user_role not in ['Admin', 'Alocacao']:
            return jsonify({'error': 'Acesso negado: somente Admins e Alocacao podem ver estatísticas'}), 403

        # Obter o id_usuario da query string se fornecido
        user_id = request.args.get('id_usuario')

        # Construir a query base
        query = """
            SELECT 
                c.risk_profile as perfil_risco,
                COUNT(*) as quantidade_clientes
            FROM client c
        """
        
        # Adicionar filtro por usuário se fornecido
        if user_id:
            query += " WHERE c.user_id = %s"
            cursor.execute(query + " GROUP BY c.risk_profile", (user_id,))
        else:
            cursor.execute(query + " GROUP BY c.risk_profile")
            
        estatisticas = cursor.fetchall()

        # Formatar a resposta para garantir que todos os perfis estejam presentes
        perfis = ['Conservador', 'Moderado', 'Sofisticado']
        resultado = []
        
        for perfil in perfis:
            perfil_stats = next((stat for stat in estatisticas if stat['perfil_risco'] == perfil), None)
            resultado.append({
                'perfil_risco': perfil,
                'quantidade_clientes': perfil_stats['quantidade_clientes'] if perfil_stats else 0
            })

        return jsonify({'estatisticas': resultado}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar estatísticas de perfil de risco: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@statistics_bp.route('/api/estatisticas/clientes-tempo', methods=['GET'])
@token_required
def get_estatisticas_clientes_tempo(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin ou Alocacao
        if request.user_role not in ['Admin', 'Alocacao']:
            return jsonify({'error': 'Acesso negado: somente Admins e Alocacao podem ver estatísticas'}), 403

        # Obter parâmetros da query string
        user_id = request.args.get('id_usuario')
        perfil = request.args.get('perfil')

        # Construir a query base
        query = """
            SELECT 
                DATE_FORMAT(c.created_at, '%Y-%m') as data,
                COUNT(*) as quantidade
            FROM client c
        """
        
        # Adicionar filtros se fornecidos
        conditions = []
        params = []
        
        if user_id:
            conditions.append("c.user_id = %s")
            params.append(user_id)
            
        if perfil:
            conditions.append("c.risk_profile = %s")
            params.append(perfil)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " GROUP BY DATE_FORMAT(c.created_at, '%Y-%m') ORDER BY data"
        
        cursor.execute(query, params)
        estatisticas = cursor.fetchall()

        return jsonify({'estatisticas': estatisticas}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar estatísticas temporais: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@statistics_bp.route('/api/estatisticas/usuarios/por-perfil/<perfil>', methods=['GET'])
@token_required
def get_usuarios_por_perfil(perfil, current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin ou Alocacao
        if request.user_role not in ['Admin', 'Alocacao']:
            return jsonify({'error': 'Acesso negado: somente Admins e Alocacao podem ver estatísticas'}), 403

        # Query para buscar usuários e quantidade de clientes por perfil
        query = """
            SELECT 
                u.id as id_usuario,
                u.username as nome_usuario,
                COUNT(c.id) as quantidade_clientes
            FROM user u
            LEFT JOIN client c ON u.id = c.user_id AND c.risk_profile = %s
            GROUP BY u.id, u.username
            ORDER BY u.username
        """
        
        cursor.execute(query, (perfil,))
        estatisticas = cursor.fetchall()

        return jsonify({'estatisticas': estatisticas}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar usuários por perfil: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close() 