from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error

history_bp = Blueprint('history', __name__)

@history_bp.route('/history', methods=['GET'])
@token_required
def get_history():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT ch.id, ch.user_id, ch.client_id, ch.action_type, ch.action_date, ch.details, c.client_name
            FROM client_history ch
            LEFT JOIN client c ON ch.client_id = c.id
            WHERE ch.user_id = %s
            ORDER BY ch.action_date DESC
        """
        cursor.execute(query, (request.user_id,))
        history = cursor.fetchall()

        return jsonify({'history': history}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar histórico: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@history_bp.route('/system-history', methods=['GET'])
@token_required
def get_system_history():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        if request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado: somente Admins podem ver o histórico do sistema'}), 403

        query = """
            SELECT ch.id, ch.user_id, u.username, ch.action_type, ch.details, ch.action_date
            FROM client_history ch
            JOIN user u ON ch.user_id = u.id
            ORDER BY ch.action_date DESC
        """
        cursor.execute(query)
        history = cursor.fetchall()

        return jsonify({'history': history}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar histórico do sistema: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()