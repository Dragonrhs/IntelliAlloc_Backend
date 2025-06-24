from flask import Blueprint, request, jsonify
from mysql.connector import Error
from datetime import datetime
import json

from utils.db import get_db_connection
from middleware.auth import token_required

history_bp = Blueprint('history', __name__)

@history_bp.route('/history', methods=['GET'])
@token_required
def get_history(current_user=None):
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
def get_legacy_system_history(current_user=None):
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

@history_bp.route('/api/history/client', methods=['GET'])
@token_required
def get_client_history(current_user=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Obter parâmetros de consulta
        client_id = request.args.get('client_id')
        limit = request.args.get('limit', default=10, type=int)
        offset = request.args.get('offset', default=0, type=int)
        
        # Construir a consulta base
        query = """
            SELECT 
                h.id, 
                h.action_type, 
                h.action_date, 
                h.details,
                u.username as user_name,
                c.client_name
            FROM 
                client_history h
            JOIN 
                user u ON h.user_id = u.id
            LEFT JOIN 
                client c ON h.client_id = c.id
        """
        
        params = []
        
        # Adicionar filtro por cliente se especificado
        if client_id:
            query += " WHERE h.client_id = %s"
            params.append(client_id)
            
        # Adicionar ordenação e paginação
        query += " ORDER BY h.action_date DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        history = cursor.fetchall()
        
        # Converter detalhes de JSON para dicionário
        for item in history:
            if item['details']:
                try:
                    item['details'] = json.loads(item['details'])
                except:
                    pass
        
        # Obter contagem total para paginação
        count_query = "SELECT COUNT(*) as total FROM client_history"
        if client_id:
            count_query += " WHERE client_id = %s"
            cursor.execute(count_query, [client_id])
        else:
            cursor.execute(count_query)
            
        total = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'history': history,
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200
        
    except Error as e:
        print(f"Erro ao buscar histórico: {str(e)}")
        return jsonify({'error': 'Erro ao buscar histórico'}), 500

@history_bp.route('/api/history/system', methods=['GET'])
@token_required
def get_general_system_history(current_user=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Obter parâmetros de consulta
        limit = request.args.get('limit', default=50, type=int)
        offset = request.args.get('offset', default=0, type=int)
        action_type = request.args.get('action_type')
        
        # Construir a consulta base para histórico de clientes
        client_query = """
            SELECT 
                'client' as history_type,
                h.id, 
                h.action_type, 
                h.action_date, 
                h.details,
                u.username as user_name,
                c.client_name,
                NULL as ativo_nome
            FROM 
                client_history h
            JOIN 
                user u ON h.user_id = u.id
            LEFT JOIN 
                client c ON h.client_id = c.id
        """
        
        # Construir a consulta base para histórico de ativos
        ativo_query = """
            SELECT 
                'ativo' as history_type,
                h.id, 
                h.action_type, 
                h.action_date, 
                h.changes as details,
                u.username as user_name,
                NULL as client_name,
                a.nome as ativo_nome
            FROM 
                ativo_history h
            JOIN 
                user u ON h.user_id = u.id
            JOIN 
                ativos a ON h.ativo_id = a.id
        """
        
        params = []
        
        # Adicionar filtro por tipo de ação se especificado
        if action_type:
            client_query += " WHERE h.action_type = %s"
            ativo_query += " WHERE h.action_type = %s"
            params.append(action_type)
            params.append(action_type)
            
        # Combinar as consultas
        query = f"({client_query}) UNION ({ativo_query}) ORDER BY action_date DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        history = cursor.fetchall()
        
        # Converter detalhes de JSON para dicionário
        for item in history:
            if item['details']:
                try:
                    item['details'] = json.loads(item['details'])
                except:
                    pass
        
        # Obter contagem total para paginação
        count_client_query = "SELECT COUNT(*) as total FROM client_history"
        count_ativo_query = "SELECT COUNT(*) as total FROM ativo_history"
        
        if action_type:
            count_client_query += " WHERE action_type = %s"
            count_ativo_query += " WHERE action_type = %s"
            cursor.execute(count_client_query, [action_type])
            client_total = cursor.fetchone()['total']
            cursor.execute(count_ativo_query, [action_type])
            ativo_total = cursor.fetchone()['total']
        else:
            cursor.execute(count_client_query)
            client_total = cursor.fetchone()['total']
            cursor.execute(count_ativo_query)
            ativo_total = cursor.fetchone()['total']
            
        total = client_total + ativo_total
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'history': history,
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200
        
    except Error as e:
        print(f"Erro ao buscar histórico do sistema: {str(e)}")
        return jsonify({'error': 'Erro ao buscar histórico do sistema'}), 500

@history_bp.route('/api/history/classificacao', methods=['GET'])
@token_required
def get_classificacao_history(current_user=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Obter parâmetros de consulta
        ativo_id = request.args.get('ativo_id')
        limit = request.args.get('limit', default=50, type=int)
        offset = request.args.get('offset', default=0, type=int)
        
        # Construir a consulta base - Usando DISTINCT ON para evitar duplicações
        query = """
            SELECT DISTINCT
                h.id, 
                h.action_type, 
                h.action_date, 
                h.changes,
                u.username as user_name,
                u.id as user_id,
                a.id as ativo_id,
                a.nome as ativo_nome,
                a.classe as ativo_classe
            FROM 
                ativo_history h
            JOIN 
                user u ON h.user_id = u.id
            JOIN 
                ativos a ON h.ativo_id = a.id
            WHERE 
                h.action_type IN ('CLASSIFICACAO', 'UPDATE_CLASSIFICACAO', 'DELETE_CLASSIFICACAO')
        """
        
        params = []
        
        # Adicionar filtro por ativo se especificado
        if ativo_id:
            query += " AND h.ativo_id = %s"
            params.append(ativo_id)
            
        # Adicionar ordenação e paginação
        query += " ORDER BY h.action_date DESC, h.id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        cursor.execute(query, params)
        history = cursor.fetchall()
        
        # Remover entradas duplicadas (mesmo ativo_id e data próxima)
        filtered_history = []
        seen_entries = set()
        
        for item in history:
            # Criar uma chave única baseada no ativo_id e na data arredondada para o minuto mais próximo
            action_date = item['action_date']
            if isinstance(action_date, str):
                try:
                    action_date = datetime.fromisoformat(action_date.replace('Z', '+00:00'))
                except ValueError:
                    # Fallback para o formato datetime padrão
                    action_date = datetime.strptime(action_date, '%Y-%m-%d %H:%M:%S')
            
            # Arredondar para o minuto mais próximo
            minute_key = f"{item['ativo_id']}_{action_date.strftime('%Y-%m-%d %H:%M')}"
            
            if minute_key not in seen_entries:
                seen_entries.add(minute_key)
                filtered_history.append(item)
        
        # Processar os resultados para extrair informações de classificação
        for item in filtered_history:
            if item['changes']:
                try:
                    changes = json.loads(item['changes'])
                    
                    # Extrair informações relevantes com base no tipo de ação
                    if item['action_type'] == 'CLASSIFICACAO':
                        if isinstance(changes, dict) and 'novo' in changes:
                            # Novo formato padronizado
                            item['classe_investimento'] = changes['novo'].get('classe_investimento', 'N/A')
                            item['indexador_primario'] = changes['novo'].get('indexador_primario', 'N/A')
                            item['tipo_indexador'] = changes['novo'].get('tipo_indexador', 'N/A')
                        else:
                            # Formato antigo (para compatibilidade)
                            item['classe_investimento'] = changes.get('classe_investimento', 'N/A')
                            item['indexador_primario'] = changes.get('indexador_primario', 'N/A')
                            item['tipo_indexador'] = changes.get('tipo_indexador', 'N/A')
                    
                    elif item['action_type'] == 'UPDATE_CLASSIFICACAO':
                        if isinstance(changes, dict) and 'novo' in changes:
                            item['classe_investimento'] = changes['novo'].get('classe_investimento', 'N/A')
                            item['indexador_primario'] = changes['novo'].get('indexador_primario', 'N/A')
                            item['tipo_indexador'] = changes['novo'].get('tipo_indexador', 'N/A')
                            
                            # Adicionar valores anteriores para comparação
                            if 'anterior' in changes:
                                item['classe_investimento_anterior'] = changes['anterior'].get('classe_investimento', 'N/A')
                                item['indexador_primario_anterior'] = changes['anterior'].get('indexador_primario', 'N/A')
                                item['tipo_indexador_anterior'] = changes['anterior'].get('tipo_indexador', 'N/A')
                    
                    elif item['action_type'] == 'DELETE_CLASSIFICACAO':
                        if isinstance(changes, dict) and 'anterior' in changes:
                            # Novo formato padronizado
                            item['classe_investimento'] = changes['anterior'].get('classe_investimento', 'N/A')
                            item['indexador_primario'] = changes['anterior'].get('indexador_primario', 'N/A')
                            item['tipo_indexador'] = changes['anterior'].get('tipo_indexador', 'N/A')
                        else:
                            # Formato antigo (para compatibilidade)
                            item['classe_investimento'] = changes.get('classe_investimento', 'N/A')
                            item['indexador_primario'] = changes.get('indexador_primario', 'N/A')
                            item['tipo_indexador'] = changes.get('tipo_indexador', 'N/A')
                        item['acao'] = 'Classificação removida'
                        
                except Exception as e:
                    print(f"Erro ao processar changes: {str(e)}")
                    item['erro_processamento'] = 'Erro ao processar dados de alteração'
        
        # Obter contagem total para paginação
        count_query = """
            SELECT COUNT(*) as total 
            FROM ativo_history 
            WHERE action_type IN ('CLASSIFICACAO', 'UPDATE_CLASSIFICACAO', 'DELETE_CLASSIFICACAO')
        """
        
        if ativo_id:
            count_query += " AND ativo_id = %s"
            cursor.execute(count_query, [ativo_id])
        else:
            cursor.execute(count_query)
            
        total = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'history': filtered_history,
            'total': total,
            'limit': limit,
            'offset': offset
        }), 200
        
    except Error as e:
        print(f"Erro ao buscar histórico de classificação: {str(e)}")
        return jsonify({'error': 'Erro ao buscar histórico de classificação'}), 500