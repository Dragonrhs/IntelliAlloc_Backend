from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from middleware.auth import token_required
from mysql.connector import Error

parametros_bp = Blueprint('parametros', __name__)

# Rotas para Parâmetros de Rebalanceamento
@parametros_bp.route('/api/parametros', methods=['GET'])
@token_required
def get_parametros(current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, nome_parametro, descricao, peso_padrao, ativo
            FROM parametros_rebalanceamento
            ORDER BY nome_parametro
        """)
        parametros = cursor.fetchall()
        
        return jsonify({'parametros': parametros}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar parâmetros: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/parametros', methods=['POST'])
@token_required
def adicionar_parametro(current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem adicionar parâmetros'}), 403

    data = request.get_json()
    if not data or 'nome_parametro' not in data or 'descricao' not in data:
        return jsonify({'error': 'Nome e descrição do parâmetro são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO parametros_rebalanceamento 
            (nome_parametro, descricao, peso_padrao, ativo)
            VALUES (%s, %s, %s, %s)
        """, (
            data['nome_parametro'],
            data['descricao'],
            data.get('peso_padrao', 1.0),
            data.get('ativo', True)
        ))

        connection.commit()
        parametro_id = cursor.lastrowid

        return jsonify({
            'message': 'Parâmetro adicionado com sucesso',
            'parametro_id': parametro_id
        }), 201

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao adicionar parâmetro: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/parametros/<int:parametro_id>', methods=['PUT'])
@token_required
def editar_parametro(parametro_id, current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem editar parâmetros'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Dados do parâmetro são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("""
            UPDATE parametros_rebalanceamento 
            SET nome_parametro = %s,
                descricao = %s,
                peso_padrao = %s,
                ativo = %s
            WHERE id = %s
        """, (
            data.get('nome_parametro'),
            data.get('descricao'),
            data.get('peso_padrao'),
            data.get('ativo'),
            parametro_id
        ))

        if cursor.rowcount == 0:
            return jsonify({'error': 'Parâmetro não encontrado'}), 404

        connection.commit()

        return jsonify({
            'message': 'Parâmetro atualizado com sucesso'
        }), 200

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao atualizar parâmetro: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/parametros/<int:parametro_id>', methods=['DELETE'])
@token_required
def excluir_parametro(parametro_id, current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem excluir parâmetros'}), 403

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("DELETE FROM parametros_rebalanceamento WHERE id = %s", (parametro_id,))

        if cursor.rowcount == 0:
            return jsonify({'error': 'Parâmetro não encontrado'}), 404

        connection.commit()

        return jsonify({
            'message': 'Parâmetro excluído com sucesso'
        }), 200

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao excluir parâmetro: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

# Rotas para Avaliação de Parâmetros por Classe
@parametros_bp.route('/api/avaliacao-parametros/<mes>', methods=['GET'])
@token_required
def get_avaliacao_parametros(mes, current_user=None):
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT apc.id, apc.mes_referencia, apc.classe_ativo, apc.parametro_id,
                   apc.nome_parametro, pr.peso_padrao as peso, apc.nota
            FROM avaliacao_parametros_classe apc
            LEFT JOIN parametros_rebalanceamento pr ON apc.parametro_id = pr.id
            WHERE apc.mes_referencia = %s
            ORDER BY apc.classe_ativo, apc.nome_parametro
        """, (mes,))
        
        avaliacoes = cursor.fetchall()
        
        return jsonify({'avaliacoes': avaliacoes}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar avaliações: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/avaliacao-parametros', methods=['POST'])
@token_required
def adicionar_avaliacao_parametros(current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem adicionar avaliações'}), 403

    data = request.get_json()
    if not data or 'mes_referencia' not in data or 'avaliacoes' not in data:
        return jsonify({'error': 'Mês de referência e avaliações são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Buscar os nomes dos parâmetros
        parametros_ids = {avaliacao['parametro_id'] for avaliacao in data['avaliacoes']}
        cursor.execute("""
            SELECT id, nome_parametro
            FROM parametros_rebalanceamento
            WHERE id IN (%s)
        """ % ','.join(['%s'] * len(parametros_ids)), tuple(parametros_ids))
        parametros_map = {row[0]: row[1] for row in cursor.fetchall()}

        # Inserir cada avaliação
        for avaliacao in data['avaliacoes']:
            cursor.execute("""
                INSERT INTO avaliacao_parametros_classe 
                (mes_referencia, classe_ativo, parametro_id, nome_parametro, nota)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    nome_parametro = VALUES(nome_parametro),
                    nota = VALUES(nota)
            """, (
                data['mes_referencia'],
                avaliacao['classe_ativo'],
                avaliacao['parametro_id'],
                parametros_map.get(avaliacao['parametro_id'], ''),
                avaliacao['nota']
            ))

        connection.commit()

        return jsonify({
            'message': f'Avaliações do mês {data["mes_referencia"]} adicionadas com sucesso'
        }), 201

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao adicionar avaliações: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/avaliacao-parametros/<int:avaliacao_id>', methods=['PUT'])
@token_required
def editar_avaliacao_parametros(avaliacao_id, current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem editar avaliações'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Dados da avaliação são obrigatórios'}), 400

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("""
            UPDATE avaliacao_parametros_classe 
            SET peso = %s,
                nota = %s
            WHERE id = %s
        """, (
            data.get('peso'),
            data.get('nota'),
            avaliacao_id
        ))

        if cursor.rowcount == 0:
            return jsonify({'error': 'Avaliação não encontrada'}), 404

        connection.commit()

        return jsonify({
            'message': 'Avaliação atualizada com sucesso'
        }), 200

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao atualizar avaliação: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@parametros_bp.route('/api/avaliacao-parametros/<int:avaliacao_id>', methods=['DELETE'])
@token_required
def excluir_avaliacao_parametros(avaliacao_id, current_user=None):
    # Verificar se o usuário tem permissão
    if request.user_role not in ['Admin', 'Alocacao']:
        return jsonify({'error': 'Acesso negado: somente usuários com cargo de Admin ou Alocacao podem excluir avaliações'}), 403

    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        cursor.execute("DELETE FROM avaliacao_parametros_classe WHERE id = %s", (avaliacao_id,))

        if cursor.rowcount == 0:
            return jsonify({'error': 'Avaliação não encontrada'}), 404

        connection.commit()

        return jsonify({
            'message': 'Avaliação excluída com sucesso'
        }), 200

    except Error as e:
        connection.rollback()
        return jsonify({'error': f'Erro ao excluir avaliação: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close() 