from flask import Blueprint, request, jsonify, make_response
from utils.db import get_db_connection
from utils.email import send_verification_email
from mysql.connector import Error
import bcrypt
import uuid
import datetime
import random
import string

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

        query = "INSERT INTO user (username, email, password_hash, role) VALUES (%s, %s, %s, %s)"
        cursor.execute(query, (username, email, hashed_password, 'Membro'))
        connection.commit()
        
        return jsonify({'message': 'Usuário registrado com sucesso'}), 201

    except Error as e:
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

        cursor = connection.cursor()
        
        cursor.execute("SELECT id, password_hash, role FROM user WHERE username = %s", (username,))
        user = cursor.fetchone()

        if user and bcrypt.checkpw(password.encode('utf-8'), user[1].encode('utf-8')):
            session_token = str(uuid.uuid4())
            expires_at = datetime.datetime.now() + datetime.timedelta(hours=24)
            
            cursor.execute("""
                INSERT INTO sessions (user_id, session_token, expires_at) 
                VALUES (%s, %s, %s)
            """, (user[0], session_token, expires_at))
            connection.commit()

            response = make_response(jsonify({
                'message': 'Login bem-sucedido',
                'expires_at': expires_at.strftime('%Y-%m-%d %H:%M:%S'),
                'role': user[2]
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
    from middleware.auth import token_required
    @token_required
    def logout_protected():
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