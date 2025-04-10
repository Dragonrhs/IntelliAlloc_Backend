from flask import Blueprint, request, jsonify
from middleware.auth import token_required
from utils.db import get_db_connection
from mysql.connector import Error
from datetime import datetime
import pandas as pd
import re

ativos_bp = Blueprint('ativos', __name__)

def calcular_prazo_restante(data, prazo):
    data_inserida = pd.to_datetime(data, format='%Y-%m-%d')
    prazo_total = int(float(prazo))
    data_atual = pd.to_datetime('today')
    
    data_prazo_total = data_inserida + pd.DateOffset(months=prazo_total)
    prazo_restante = (data_prazo_total - data_atual).days
    
    return int(prazo_restante)

def limpar_cnpj(cnpj):
    """Remove todos os caracteres não numéricos do CNPJ"""
    return re.sub(r'[^0-9]', '', cnpj)

def verificar_duplicidade(cursor, data, ticker, isin, cnpj, status):
    """Verifica duplicidade e data do ativo"""
    data_inserida = pd.to_datetime(data)
    
    # Determina o campo de busca com base na prioridade
    if ticker:
        query = "SELECT data, status FROM ativos WHERE ticker = %s"
        params = (ticker,)
        campo = 'ticker'
        valor = ticker
    elif isin:
        query = "SELECT data, status FROM ativos WHERE isin = %s"
        params = (isin,)
        campo = 'isin'
        valor = isin
    elif cnpj:
        query = "SELECT data, status FROM ativos WHERE cnpj = %s"
        params = (cnpj,)
        campo = 'cnpj'
        valor = cnpj
    else:
        return False, "É necessário fornecer pelo menos um identificador: ticker, ISIN ou CNPJ"

    cursor.execute(query, params)
    resultados = cursor.fetchall()
    
    if resultados:
        # Converter resultados para DataFrame
        df = pd.DataFrame(resultados, columns=['data', 'status'])
        df['data'] = pd.to_datetime(df['data'])
        
        # Verifica se existe ativo com mesma data e status
        if any((df['data'] == data_inserida) & (df['status'] == status)):
            return False, f"O ativo com {campo} {valor} já foi inserido com a mesma data e status"
        
        # Verifica se a data é menor que a data mais recente
        data_mais_recente = df['data'].max()
        if data_inserida < data_mais_recente:
            return False, f"A data do ativo com {campo} {valor} não pode ser menor que a data mais recente ({data_mais_recente.strftime('%d/%m/%Y')}) do ativo no BD"
    
    return True, None

@ativos_bp.route('/api/ativos', methods=['POST'])
@token_required
def inserir_ativo():
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem inserir ativos'}), 403

        data = request.get_json()
        
        # Validar campos obrigatórios
        campos_obrigatorios = [
            'nome', 'classe', 'canal', 'emissor', 'risco_credito',
            'cnpj', 'gestora', 'prazo_total', 'data',
            'status', 'emissor_emissao',
            'analista_responsavel', 'perfil', 'master_feeder',
            'restrito_alocacao'
        ]
        
        for campo in campos_obrigatorios:
            if campo not in data:
                return jsonify({'error': f'Campo obrigatório não fornecido: {campo}'}), 400

        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()

        # Tratar campos que podem ser NULL
        master_feeder = data.get('master_feeder')
        if master_feeder == '':
            master_feeder = None

        emissor_emissao = data.get('emissor_emissao')
        if emissor_emissao == '':
            emissor_emissao = None

        # Tratar campo restrito_alocacao
        restrito_alocacao = data.get('restrito_alocacao')
        if restrito_alocacao != 'Restrito':
            restrito_alocacao = None

        # Limpar CNPJ
        cnpj = limpar_cnpj(data.get('cnpj', ''))

        # Tratar ISIN vazio
        isin = data.get('isin')
        if not isin or isin.strip() == '':
            isin = None

        # Tratar ticker vazio
        ticker = data.get('ticker')
        if not ticker or ticker.strip() == '':
            ticker = None

        # Verificar duplicidade e data
        valido, mensagem = verificar_duplicidade(
            cursor,
            data.get('data'),
            ticker,
            isin,
            cnpj,
            data.get('status')
        )
        
        if not valido:
            return jsonify({'error': mensagem}), 400

        # Calcular prazo restante usando a nova função
        prazo_restante = calcular_prazo_restante(data.get('data'), data.get('prazo_total'))

        query = """
            INSERT INTO ativos (
                data, nome, classe, canal, emissor, risco_credito,
                ticker, isin, cnpj, gestora, prazo_total,
                prazo_restante, status, emissor_emissao,
                analista_responsavel, perfil, master_feeder,
                restrito_alocacao
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
        """

        cursor.execute(query, (
            data.get('data'),
            data.get('nome'),
            data.get('classe'),
            data.get('canal'),
            data.get('emissor'),
            data.get('risco_credito'),
            ticker,
            isin,
            cnpj,
            data.get('gestora'),
            data.get('prazo_total'),
            prazo_restante,
            data.get('status'),
            emissor_emissao,
            data.get('analista_responsavel'),
            data.get('perfil'),
            master_feeder,
            restrito_alocacao
        ))

        connection.commit()
        return jsonify({'message': 'Ativo inserido com sucesso'}), 201

    except Error as e:
        return jsonify({'error': f'Erro ao inserir ativo: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@ativos_bp.route('/api/ativos/buscar', methods=['GET'])
@token_required
def buscar_ativos():
    tipo = request.args.get('tipo')
    valor = request.args.get('valor')
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        if tipo == 'todos':
            cursor.execute('SELECT * FROM ativos ORDER BY nome')
        elif tipo == 'isin':
            cursor.execute('SELECT * FROM ativos WHERE isin = %s', (valor,))
        elif tipo == 'cnpj':
            cursor.execute('SELECT * FROM ativos WHERE cnpj = %s', (valor,))
        elif tipo == 'ticker':
            cursor.execute('SELECT * FROM ativos WHERE ticker = %s', (valor,))
        else:
            return jsonify({'error': 'Tipo de busca inválido'}), 400
        
        ativos = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return jsonify({'ativos': ativos}), 200
        
    except Exception as e:
        print(f'Erro ao buscar ativos: {str(e)}')
        return jsonify({'error': 'Erro ao buscar ativos'}), 500

@ativos_bp.route('/api/ativos/<int:id>', methods=['PUT'])
@token_required
def atualizar_ativo(id):
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem atualizar ativos'}), 403

        data = request.get_json()
        
        # Verificar se o ativo existe
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute('SELECT * FROM ativos WHERE id = %s', (id,))
        ativo = cursor.fetchone()
        
        if not ativo:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Ativo não encontrado'}), 404
        
        # Verificar se já existe um ativo com a mesma data e status
        if 'data' in data and 'status' in data:
            cursor.execute('''
                SELECT id FROM ativos 
                WHERE data = %s 
                AND status = %s 
                AND id != %s
            ''', (data['data'], data['status'], id))
            
            if cursor.fetchone():
                cursor.close()
                conn.close()
                return jsonify({'error': 'Já existe um ativo com esta data e status'}), 400
        
        # Atualizar o ativo
        campos = ['nome', 'classe', 'canal', 'emissor', 'risco_credito', 'ticker', 'isin', 'cnpj',
                 'gestora', 'prazo_total', 'data', 'status', 'emissor_emissao', 'analista_responsavel',
                 'perfil', 'master_feeder', 'restrito_alocacao']
        
        update_fields = []
        update_values = []
        
        for campo in campos:
            if campo in data:
                update_fields.append(f"{campo} = %s")
                update_values.append(data[campo])
        
        if not update_fields:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Nenhum campo para atualizar'}), 400
        
        update_values.append(id)
        query = f"UPDATE ativos SET {', '.join(update_fields)} WHERE id = %s"
        
        cursor.execute(query, update_values)
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({'message': 'Ativo atualizado com sucesso'}), 200
        
    except Exception as e:
        print(f'Erro ao atualizar ativo: {str(e)}')
        return jsonify({'error': 'Erro ao atualizar ativo'}), 500 