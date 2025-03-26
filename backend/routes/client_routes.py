from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from utils.history import log_client_action
from middleware.auth import token_required
from mysql.connector import Error
import json

client_bp = Blueprint('client', __name__)

@client_bp.route('/add-client', methods=['POST'])
@token_required
def add_client():
    data = request.get_json()
    client_name = data.get('client_name')
    q1 = data.get('q1_investment_duration')
    q2 = data.get('q2_investment_purpose')
    q3 = data.get('q3_investment_allocation')
    q4 = data.get('q4_financial_experience')
    q5 = data.get('q5_investment_options', [])
    q6 = data.get('q6_observations', '')

    if not all([client_name, q1, q2, q3, q4]):
        return jsonify({'error': 'Campos obrigatórios não preenchidos'}), 400

    score = (
        int(q1) + int(q2) + int(q3) + int(q4) + 
        sum(1 for opt in q5 if opt in ['Ações', 'Renda Fixa', 'Fundos Imobiliários', 'ETFs', 'Criptomoedas']) * 2
    )

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        
        cursor.execute("""
            INSERT INTO client (user_id, client_name, score, q1_investment_duration, q2_investment_purpose, 
            q3_investment_allocation, q4_financial_experience, q5_investment_options, q6_observations)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (request.user_id, client_name, score, q1, q2, q3, q4, json.dumps(q5), q6))
        
        client_id = cursor.lastrowid
        connection.commit()

        log_client_action(request.user_id, client_id, 'INSERT', client_name)

        return jsonify({'message': 'Cliente adicionado com sucesso', 'client_id': client_id}), 201

    except Error as e:
        return jsonify({'error': f'Erro ao adicionar cliente: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@client_bp.route('/clients', methods=['GET'])
@token_required
def get_clients():
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, client_name, score, q1_investment_duration, q2_investment_purpose, 
                   q3_investment_allocation, q4_financial_experience, q5_investment_options, 
                   q6_observations, created_at 
            FROM client 
            WHERE user_id = %s
        """, (request.user_id,))
        clients = cursor.fetchall()
        
        for client in clients:
            client['q5_investment_options'] = json.loads(client['q5_investment_options'])
        
        return jsonify({'clients': clients}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao listar clientes: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@client_bp.route('/client/<int:client_id>', methods=['GET', 'PUT', 'DELETE'])
@token_required
def manage_client(client_id):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT * FROM client WHERE id = %s AND user_id = %s", (client_id, request.user_id))
        client = cursor.fetchone()
        if not client:
            return jsonify({'error': 'Cliente não encontrado ou não pertence ao usuário'}), 404

        if request.method == 'GET':
            client['q5_investment_options'] = json.loads(client['q5_investment_options'])
            return jsonify(client), 200

        elif request.method == 'PUT':
            data = request.get_json()
            client_name = data.get('client_name')
            q1 = data.get('q1_investment_duration')
            q2 = data.get('q2_investment_purpose')
            q3 = data.get('q3_investment_allocation')
            q4 = data.get('q4_financial_experience')
            q5 = data.get('q5_investment_options')
            q6 = data.get('q6_observations')

            if not all([client_name, q1, q2, q3, q4, q5]):
                return jsonify({'error': 'Todos os campos obrigatórios devem ser preenchidos'}), 400

            score = (
                int(q1) + int(q2) + int(q3) + int(q4) + 
                sum(1 for opt in q5 if opt in ['Ações', 'Renda Fixa', 'Fundos Imobiliários', 'ETFs', 'Criptomoedas']) * 2
            )

            cursor.execute("""
                UPDATE client 
                SET client_name = %s, score = %s, q1_investment_duration = %s, q2_investment_purpose = %s, 
                    q3_investment_allocation = %s, q4_financial_experience = %s, q5_investment_options = %s, 
                    q6_observations = %s
                WHERE id = %s AND user_id = %s
            """, (client_name, score, q1, q2, q3, q4, json.dumps(q5), q6 or None, client_id, request.user_id))
            connection.commit()

            log_client_action(request.user_id, client_id, 'UPDATE', client_name)

            return jsonify({'message': 'Cliente atualizado com sucesso'}), 200

        elif request.method == 'DELETE':
            log_client_action(request.user_id, client_id, 'DELETE', client['client_name'])
            cursor.execute("DELETE FROM client WHERE id = %s AND user_id = %s", (client_id, request.user_id))
            connection.commit()
            return jsonify({'message': 'Cliente excluído com sucesso'}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao gerenciar cliente: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()