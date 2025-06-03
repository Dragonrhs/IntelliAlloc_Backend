from functools import wraps
from flask import request, jsonify
from utils.db import get_db_connection
import mysql.connector
from mysql.connector import Error

def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.cookies.get('session_token')
        if not token:
            return jsonify({'error': 'Token de autenticação necessário'}), 401

        try:
            connection = get_db_connection()
            if connection is None:
                return jsonify({'error': 'Erro de conexão com o banco'}), 500

            cursor = connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT user_id FROM sessions 
                WHERE session_token = %s 
                AND expires_at > NOW()
            """, (token,))
            session_data = cursor.fetchone()

            if not session_data:
                return jsonify({'error': 'Token inválido ou expirado'}), 401
            
            user_id = session_data['user_id']
            request.user_id = user_id
            
            # Buscar informações do usuário incluindo cargo
            cursor.execute("""
                SELECT u.id, u.cargo_id, c.nome as cargo_nome
                FROM user u
                JOIN cargos c ON u.cargo_id = c.id
                WHERE u.id = %s
            """, (user_id,))
            user_data = cursor.fetchone()
            
            if not user_data:
                return jsonify({'error': 'Usuário não encontrado'}), 404
                
            # Adicionar cargo_id e cargo_nome ao request para verificações futuras
            request.user_cargo_id = user_data['cargo_id']
            request.user_role = user_data['cargo_nome']  # Mantemos user_role para compatibilidade
            
            # Adicionar o usuário completo aos kwargs para o middleware de permissões
            kwargs['current_user'] = {
                'id': user_id,
                'cargo_id': user_data['cargo_id'],
                'cargo_nome': user_data['cargo_nome']
            }

        except Error as e:
            return jsonify({'error': f'Erro ao verificar token: {str(e)}'}), 500
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()

        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(request, 'user_role') or request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado. Apenas administradores podem acessar este recurso.'}), 403
        return f(*args, **kwargs)
    return decorated_function