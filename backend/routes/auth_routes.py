from flask import Blueprint, request, jsonify, make_response
from utils.db import get_db_connection
from utils.email import send_verification_email
from mysql.connector import Error
import bcrypt
import uuid
import datetime
import random
import string
from middleware.auth import token_required

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not all([username, email, password]):
        return jsonify({'error': 'Username, email e senha são obrigatórios'}), 400

    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        
        cursor.execute("SELECT id FROM user WHERE username = %s OR email = %s", (username, email))
        if cursor.fetchone():
            return jsonify({'error': 'Username ou email já existe'}), 400

        # Obter o cargo_id de "Membro"
        cursor.execute("SELECT id FROM cargos WHERE nome = 'Membro'")
        cargo_id = cursor.fetchone()[0]

        query = "INSERT INTO user (username, email, password_hash, cargo_id) VALUES (%s, %s, %s, %s)"
        cursor.execute(query, (username, email, hashed_password, cargo_id))
        user_id = cursor.lastrowid
        
        # Copiar as permissões do cargo para o usuário
        cursor.execute("""
            SELECT funcionalidade_id 
            FROM permissoes_cargos
            WHERE cargo_id = %s
        """, (cargo_id,))
        
        permissoes_cargo = cursor.fetchall()
        
        # Inserir as permissões para o novo usuário
        for permissao in permissoes_cargo:
            cursor.execute("""
                INSERT INTO permissoes_usuarios (user_id, funcionalidade_id, permitido)
                VALUES (%s, %s, TRUE)
            """, (user_id, permissao[0]))
        
        connection.commit()
        
        return jsonify({
            'message': 'Usuário registrado com sucesso',
            'permissoes_copiadas': len(permissoes_cargo)
        }), 201

    except Error as e:
        if connection:
            connection.rollback()
        return jsonify({'error': f'Erro ao registrar usuário: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not all([username, password]):
        return jsonify({'error': 'Username e senha são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Buscar informações do usuário incluindo o cargo
        cursor.execute("""
            SELECT u.id, u.password_hash, u.cargo_id, c.nome as cargo_nome 
            FROM user u
            JOIN cargos c ON u.cargo_id = c.id
            WHERE u.username = %s
        """, (username,))
        user = cursor.fetchone()

        if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
            session_token = str(uuid.uuid4())
            expires_at = datetime.datetime.now() + datetime.timedelta(hours=24)
            
            cursor.execute("""
                INSERT INTO sessions (user_id, session_token, expires_at) 
                VALUES (%s, %s, %s)
            """, (user['id'], session_token, expires_at))
            connection.commit()

            response = make_response(jsonify({
                'message': 'Login bem-sucedido',
                'expires_at': expires_at.strftime('%Y-%m-%d %H:%M:%S'),
                'cargo_id': user['cargo_id'],
                'cargo_nome': user['cargo_nome'],
                'user_id': user['id']
            }), 200)
            response.set_cookie(
                'session_token',
                session_token,
                httponly=True,
                secure=False,
                samesite='Lax',
                expires=expires_at
            )
            return response
        else:
            return jsonify({'error': 'Credenciais inválidas'}), 401

    except Error as e:
        return jsonify({'error': f'Erro ao fazer login: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@auth_bp.route('/logout', methods=['POST'])
def logout():
    @token_required
    def logout_protected(current_user=None):
        token = request.cookies.get('session_token')
        
        try:
            connection = get_db_connection()
            if connection is None:
                return jsonify({'error': 'Erro de conexão com o banco'}), 500

            cursor = connection.cursor()
            cursor.execute("DELETE FROM sessions WHERE session_token = %s", (token,))
            connection.commit()
            
            response = make_response(jsonify({'message': 'Logout bem-sucedido'}), 200)
            response.set_cookie('session_token', '', expires=0)
            return response

        except Error as e:
            return jsonify({'error': f'Erro ao fazer logout: {str(e)}'}), 500
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    return logout_protected()

@auth_bp.route('/request-password-reset', methods=['POST'])
def request_password_reset():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({'error': 'E-mail é obrigatório'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("SELECT id FROM user WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'error': 'E-mail não encontrado'}), 404

        token = ''.join(random.choices(string.digits, k=6))
        expires_at = datetime.datetime.now() + datetime.timedelta(minutes=10)

        cursor.execute("""
            INSERT INTO password_reset_tokens (user_id, token, expires_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE token = %s, expires_at = %s
        """, (user[0], token, expires_at, token, expires_at))
        connection.commit()

        if send_verification_email(email, token):
            return jsonify({'message': 'Código de verificação enviado para o e-mail'}), 200
        else:
            return jsonify({'error': 'Erro ao enviar o e-mail'}), 500

    except Error as e:
        return jsonify({'error': f'Erro ao processar solicitação: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    email = data.get('email')
    token = data.get('token')
    new_password = data.get('new_password')

    if not all([email, token, new_password]):
        return jsonify({'error': 'E-mail, código e nova senha são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("""
            SELECT user_id FROM password_reset_tokens 
            WHERE token = %s AND expires_at > NOW()
        """, (token,))
        token_data = cursor.fetchone()

        if not token_data:
            return jsonify({'error': 'Código inválido ou expirado'}), 401

        cursor.execute("SELECT id FROM user WHERE email = %s", (email,))
        user = cursor.fetchone()

        if not user or user[0] != token_data[0]:
            return jsonify({'error': 'E-mail não corresponde ao código'}), 401

        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), salt)
        cursor.execute("UPDATE user SET password_hash = %s WHERE id = %s", (hashed_password, user[0]))

        cursor.execute("DELETE FROM password_reset_tokens WHERE token = %s", (token,))
        connection.commit()

        return jsonify({'message': 'Senha redefinida com sucesso'}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao redefinir senha: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@auth_bp.route('/me', methods=['GET'])
@token_required
def get_current_user(current_user):
    """
    Retorna as informações do usuário logado
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Buscar informações do usuário incluindo o cargo
        cursor.execute("""
            SELECT u.id, u.username, u.email, c.nome as role, u.cargo_id
            FROM user u
            LEFT JOIN cargos c ON u.cargo_id = c.id
            WHERE u.id = %s
        """, (current_user['id'],))
        
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'message': 'Usuário não encontrado'}), 404
            
        return jsonify(user)
        
    except Exception as e:
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()