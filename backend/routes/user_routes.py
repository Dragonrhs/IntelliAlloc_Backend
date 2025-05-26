from flask import Blueprint, request, jsonify, make_response
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error
import datetime

user_bp = Blueprint('user', __name__)

@user_bp.route('/home', methods=['GET'])
@token_required
def home(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Buscar informações do usuário incluindo o cargo
        cursor.execute("""
            SELECT u.username, u.email, u.created_at, u.cargo_id, c.nome as cargo_nome 
            FROM user u
            JOIN cargos c ON u.cargo_id = c.id
            WHERE u.id = %s
        """, (request.user_id,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        return jsonify({
            'message': f'Bem-vindo {user["username"]}!',
            'email': user["email"],
            'created_at': user["created_at"].strftime('%Y-%m-%d %H:%M:%S'),
            'last_access': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cargo_id': user["cargo_id"],
            'cargo_nome': user["cargo_nome"],
            'user_id': request.user_id
        }), 200

    except Error as e:
        return jsonify({'error': f'Erro ao acessar home: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/update-user', methods=['PUT'])
@token_required
def update_user(current_user=None):
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
def delete_user(current_user=None):
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
def get_users(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin
        if request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado: somente Admins podem listar usuários'}), 403

        # Buscar todos os usuários incluindo informações do cargo
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.cargo_id, c.nome as cargo_nome 
            FROM user u
            JOIN cargos c ON u.cargo_id = c.id
            ORDER BY u.username
        """)
        users = cursor.fetchall()

        return jsonify(users), 200

    except Error as e:
        return jsonify({'error': f'Erro ao listar usuários: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@user_bp.route('/users/<int:user_id>/role', methods=['PUT'])
@token_required
def update_user_role(user_id, current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o usuário é Admin
        if request.user_role != 'Admin':
            return jsonify({'error': 'Acesso negado: somente Admins podem alterar cargos'}), 403

        data = request.get_json()
        new_cargo_id = data.get('cargo_id')

        if not new_cargo_id:
            return jsonify({'error': 'ID do cargo é obrigatório'}), 400

        # Verificar se o cargo existe
        cursor.execute("SELECT id, nome FROM cargos WHERE id = %s", (new_cargo_id,))
        cargo = cursor.fetchone()
        if not cargo:
            return jsonify({'error': 'Cargo não encontrado'}), 404

        # Verificar se o usuário existe
        cursor.execute("SELECT id FROM user WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Usuário não encontrado'}), 404

        # Atualizar o cargo
        cursor.execute("UPDATE user SET cargo_id = %s WHERE id = %s", (new_cargo_id, user_id))
        
        # Copiar as permissões do cargo para o usuário
        
        # 1. Primeiro, remover todas as permissões existentes do usuário
        cursor.execute("DELETE FROM permissoes_usuarios WHERE user_id = %s", (user_id,))
        
        # 2. Obter todas as permissões do cargo
        cursor.execute("""
            SELECT funcionalidade_id 
            FROM permissoes_cargos
            WHERE cargo_id = %s
        """, (new_cargo_id,))
        
        permissoes_cargo = cursor.fetchall()
        
        # 3. Copiar as permissões do cargo para o usuário
        for permissao in permissoes_cargo:
            cursor.execute("""
                INSERT INTO permissoes_usuarios (user_id, funcionalidade_id, permitido)
                VALUES (%s, %s, TRUE)
            """, (user_id, permissao['funcionalidade_id']))
        
        connection.commit()

        return jsonify({
            'message': f'Cargo do usuário {user_id} atualizado para {cargo["nome"]}',
            'cargo_id': cargo["id"],
            'cargo_nome': cargo["nome"],
            'permissoes_copiadas': len(permissoes_cargo)
        }), 200

    except Error as e:
        return jsonify({'error': f'Erro ao atualizar cargo: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()