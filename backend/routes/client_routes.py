from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from utils.history import log_client_action
from middleware.auth import token_required
from mysql.connector import Error
import json

client_bp = Blueprint('client', __name__)

def calculate_score(q1, q2, q3, q4, q5):
    score = 0
    
    # Pontuação para q1 (Duração do Investimento)
    q1_scores = {
        'Até 1 ano': 5,
        'De 1 a 3 anos': 10,
        'De 3 a 5 anos': 15,
        'Acima de 5 anos': 20
    }
    score += q1_scores.get(q1, 0)

    # Pontuação para q2 (Objetivo do Investimento)
    q2_scores = {
        'Preservação de patrimônio': 5,
        'Obter retornos superiores às aplicações tradicionais, tolerando pequenas perdas de parte do patrimônio no curto prazo': 10,
        'Obter retornos superiores às aplicações tradicionais, tolerando possíveis perdas significativas de parte do patrimônio no médio prazo': 15,
        'Crescimento substancial do patrimônio no longo prazo, mesmo que a estratégia possa implicar em perdas expressivas dos recursos investidos': 20
    }
    score += q2_scores.get(q2, 0)

    # Pontuação para q3 (Alocação do Patrimônio)
    q3_scores = {
        'Menos de 25%': 5,
        'De 25% a 50%': 10,
        'De 50% a 75%': 15,
        'Acima de 75%': 20
    }
    score += q3_scores.get(q3, 0)

    # Pontuação para q4 (Experiência Financeira)
    q4_scores = {
        'Não possui nenhuma experiência': 5,
        'Pouca experiência em investimentos em geral': 10,
        'Experiência com investimentos com pouca/média probabilidade de perda': 15,
        'Se sente seguro em tomar decisões de investimentos e esta apto a entender e ponderar os riscos': 20
    }
    score += q4_scores.get(q4, 0)

    # Pontuação para q5 (Opções de Investimento)
    q5_scores = {
        'Ações': 20,
        'Derivativos/Estruturados': 20,
        'Fundos de Investimentos de Ações e Multimercados': 15,
        'Fundos de Investimentos de Renda Fixa': 10,
        'CDB': 10,
        'Previdência': 10,
        'Títulos Públicos': 5,
        'Imóveis': 5,
        'Poupança': 5,
        'Não realiza investimentos': 0
    }
    for option in q5:
        score += q5_scores.get(option, 0)

    return score

def calculate_risk_profile(score):
    if 20 <= score <= 79:
        return 'Conservador'
    elif 80 <= score <= 139:
        return 'Moderado'
    elif 140 <= score <= 180:
        return 'Sofisticado'
    return None  # Caso o score esteja fora do intervalo esperado

@client_bp.route('/add-client', methods=['POST'])
@token_required
def add_client(current_user=None):
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

    score = calculate_score(q1, q2, q3, q4, q5)
    risk_profile = calculate_risk_profile(score)

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        
        cursor.execute("""
            INSERT INTO client (user_id, client_name, score, risk_profile, q1_investment_duration, q2_investment_purpose, 
            q3_investment_allocation, q4_financial_experience, q5_investment_options, q6_observations)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (request.user_id, client_name, score, risk_profile, q1, q2, q3, q4, json.dumps(q5), q6))
        
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
def get_clients(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, client_name, score, risk_profile, q1_investment_duration, q2_investment_purpose, 
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
def manage_client(client_id, current_user=None):
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

            score = calculate_score(q1, q2, q3, q4, q5)
            risk_profile = calculate_risk_profile(score)

            cursor.execute("""
                UPDATE client 
                SET client_name = %s, score = %s, risk_profile = %s, q1_investment_duration = %s, q2_investment_purpose = %s, 
                    q3_investment_allocation = %s, q4_financial_experience = %s, q5_investment_options = %s, 
                    q6_observations = %s
                WHERE id = %s AND user_id = %s
            """, (client_name, score, risk_profile, q1, q2, q3, q4, json.dumps(q5), q6 or None, client_id, request.user_id))
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