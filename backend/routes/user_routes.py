from flask import Blueprint, request, jsonify, make_response
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error
import datetime

user_bp = Blueprint('user', __name__)

@user_bp.route('/home', methods=['GET'])
@token_required
def home():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("SELECT username, email, created_at, role FROM user WHERE id = %s", (request.user_id,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        return jsonify({
            'message': f'Bem-vindo {user[0]}!',
            'email': user[1],
            'created_at': user[2].strftime('%Y-%m-%d %H:%M:%S'),
            'last_access': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'role': user[3]
        }), 200

    except Error as e:
        return jsonify({'error': f'Erro ao acessar home: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/update-user', methods=['PUT'])
@token_required
def update_user():
    data = request.get_json()
    new_username = data.get('username')
    new_email = data.get('email')

    if not all([new_username, new_email]):
        return jsonify({'error': 'Username e e-mail são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        cursor.execute(
            "SELECT id FROM user WHERE (username = %s OR email = %s) AND id != %s",
            (new_username, new_email, request.user_id)
        )
        if cursor.fetchone():
            return jsonify({'error': 'Username ou e-mail já está em uso por outro usuário'}), 400

        cursor.execute(
            "UPDATE user SET username = %s, email = %s WHERE id = %s",
            (new_username, new_email, request.user_id)
        )
        connection.commit()

        return jsonify({'message': 'Usuário atualizado com sucesso'}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao atualizar usuário: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/delete-user', methods=['DELETE'])
@token_required
def delete_user():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        cursor.execute("DELETE FROM user WHERE id = %s", (request.user_id,))
        
        if cursor.rowcount == 0:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        cursor.execute("DELETE FROM sessions WHERE user_id = %s", (request.user_id,))
        connection.commit()

        response = make_response(jsonify({'message': 'Usuário e seus clientes excluídos com sucesso'}), 200)
        response.set_cookie('session_token', '', expires=0)
        return response

    except Error as e:
        return jsonify({'error': f'Erro ao excluir usuário: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/users', methods=['GET'])
@token_required
def get_users():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin
        if request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado: somente Admins podem listar usuários'}), 403

        # Buscar todos os usuários (username e role)
        cursor.execute("SELECT id, username, role FROM user")
        users = cursor.fetchall()

        return jsonify({'users': users}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao listar usuários: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@token_required
def update_user_role(user_id):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Verificar se o usuário é Admin
        if request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado: somente Admins podem alterar roles'}), 403

        data = request.get_json()
        new_role = data.get('role')

        # Roles predefinidos
        valid_roles = ['Admin', 'PS', 'Alocacao', 'Research', 'Membro']
        if new_role not in valid_roles:
            return jsonify({'error': f'Role inválido. Use: {", ".join(valid_roles)}'}), 400

        # Verificar se o usuário existe
        cursor.execute("SELECT id FROM user WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Usuário não encontrado'}), 404

        # Atualizar o role
        cursor.execute("UPDATE user SET role = %s WHERE id = %s", (new_role, user_id))
        connection.commit()

        return jsonify({'message': f'Role do usuário {user_id} atualizado para {new_role}'}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao atualizar role: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()