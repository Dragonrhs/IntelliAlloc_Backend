from flask import Blueprint, request, jsonify
from middleware.auth import token_required
from utils.db import get_db_connection
from mysql.connector import Error
from datetime import datetime
import pandas as pd
import re
from werkzeug.utils import secure_filename
import os
import tempfile
import json

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

def formatar_cnpj(cnpj):
    """Formata o CNPJ para o padrão XX.XXX.XXX/XXXX-XX"""
    if not cnpj or cnpj == 'nan':
        return ''
    # Remove todos os caracteres não numéricos
    cnpj = ''.join(filter(str.isdigit, str(cnpj)))
    if len(cnpj) != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"

@ativos_bp.route('/api/ativos', methods=['POST'])
@token_required
def inserir_ativo(current_user=None):
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

        # Registrar no histórico
        ativo_id = cursor.lastrowid
        cursor.execute("""
            INSERT INTO ativo_history (
                ativo_id, user_id, action_type, changes
            ) VALUES (
                %s, %s, 'INSERT', %s
            )
        """, (
            ativo_id,
            request.user_id,
            json.dumps(data)
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
def buscar_ativos(current_user=None):
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
        
        # Formatar CNPJ em todos os ativos
        for ativo in ativos:
            if 'cnpj' in ativo:
                ativo['cnpj'] = formatar_cnpj(ativo['cnpj'])
        
        cursor.close()
        conn.close()
        
        return jsonify({'ativos': ativos}), 200
        
    except Exception as e:
        print(f'Erro ao buscar ativos: {str(e)}')
        return jsonify({'error': 'Erro ao buscar ativos'}), 500

@ativos_bp.route('/api/ativos/<int:id>', methods=['PUT'])
@token_required
def atualizar_ativo(id, current_user=None):
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

        # Registrar no histórico
        cursor.execute("""
            INSERT INTO ativo_history (
                ativo_id, user_id, action_type, changes
            ) VALUES (
                %s, %s, 'UPDATE', %s
            )
        """, (
            id,
            request.user_id,
            json.dumps(data)
        ))
        
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({'message': 'Ativo atualizado com sucesso'}), 200
        
    except Exception as e:
        print(f'Erro ao atualizar ativo: {str(e)}')
        return jsonify({'error': 'Erro ao atualizar ativo'}), 500

@ativos_bp.route('/api/ativos/importar-lote', methods=['POST'])
@token_required
def importar_ativos_lote(current_user=None):
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem importar ativos em lote'}), 403

        if 'file' not in request.files:
            return jsonify({'error': 'Nenhum arquivo enviado'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Nenhum arquivo selecionado'}), 400

        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'error': 'Formato de arquivo inválido. Use .xlsx ou .xls'}), 400

        # Criar um arquivo temporário
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, secure_filename(file.filename))
        file.save(temp_path)

        # Ler o arquivo Excel
        df = pd.read_excel(temp_path)
        
        # Normalizar nomes das colunas
        df.columns = df.columns.str.lower().str.replace(' ', '_').str.replace('(', '').str.replace(')', '')
        
        # Mapear nomes de colunas específicos
        mapeamento_colunas = {
            'risco_de_credito': 'risco_credito',
            'prazo_total_meses': 'prazo_total',
            'emissor_ou_emissao': 'emissor_emissao',
            'master_ou_feeder': 'master_feeder',
            'restrito_para_alocacao': 'restrito_alocacao'
        }
        
        # Renomear colunas conforme o mapeamento
        df = df.rename(columns=mapeamento_colunas)
        
        # Converter todos os valores para string, tratando valores nulos
        for coluna in df.columns:
            df[coluna] = df[coluna].astype(str).replace('nan', '').replace('None', '')
        
        # Formatar CNPJ antes de truncar
        if 'cnpj' in df.columns:
            df['cnpj'] = df['cnpj'].apply(formatar_cnpj)
        
        # Definir tamanhos máximos para cada coluna
        tamanhos_maximos = {
            'nome': 100,
            'classe': 50,
            'canal': 20,
            'emissor': 100,
            'risco_credito': 20,
            'cnpj': 18,
            'gestora': 100,
            'prazo_total': 10,
            'data': 10,
            'status': 20,
            'emissor_emissao': 20,
            'analista_responsavel': 100,
            'perfil': 20,
            'master_feeder': 20,
            'restrito_alocacao': 20
        }
        
        # Truncar valores que excedem o tamanho máximo
        for coluna in df.columns:
            if coluna in tamanhos_maximos:
                df[coluna] = df[coluna].str.slice(0, tamanhos_maximos[coluna])

        # Definir campos obrigatórios por classe
        campos_obrigatorios_por_classe = {
            'Renda Fixa': [
                'canal', 'risco_credito', 'emissor', 'ticker', 'prazo_total',
                'status', 'data', 'emissor_emissao', 'perfil', 'analista_responsavel'
            ],
            'Fundos': [
                'cnpj', 'gestora', 'prazo_total', 'data', 'canal',
                'master_feeder', 'perfil', 'analista_responsavel', 'status'
            ],
            'Prev': [
                'cnpj', 'gestora', 'prazo_total', 'data', 'canal',
                'perfil', 'analista_responsavel', 'status'
            ],
            'Listados': [
                'ticker', 'gestora', 'prazo_total', 'data', 'canal',
                'perfil', 'status', 'analista_responsavel'
            ],
            'Cetipados': [
                'ticker', 'cnpj', 'gestora', 'prazo_total', 'data',
                'canal', 'perfil', 'status', 'analista_responsavel'
            ],
            'COE': [
                'isin', 'prazo_total', 'data', 'canal', 'perfil',
                'status', 'analista_responsavel'
            ],
            'Fundos Internacionais': [
                'isin', 'gestora', 'prazo_total', 'data', 'canal', 'perfil'
            ],
            'Renda Fixa Internacional': [
                'isin', 'risco_credito', 'emissor', 'prazo_total',
                'data', 'canal', 'emissor_emissao', 'perfil'
            ]
        }

        # Campos que podem estar em branco por classe
        campos_em_branco_por_classe = {
            'Renda Fixa': ['isin', 'cnpj', 'gestora', 'master_feeder'],
            'Fundos': ['isin', 'emissor', 'risco_credito', 'ticker', 'emissor_emissao'],
            'Prev': ['emissor', 'risco_credito', 'ticker', 'emissor_emissao', 'master_feeder'],
            'Listados': ['emissor', 'risco_credito', 'emissor_emissao', 'master_feeder'],
            'Cetipados': ['emissor', 'risco_credito', 'emissor_emissao', 'master_feeder'],
            'COE': ['ticker', 'cnpj', 'master_feeder'],
            'Fundos Internacionais': ['emissor', 'risco_credito', 'ticker', 'cnpj', 'emissor_emissao', 'master_feeder'],
            'Renda Fixa Internacional': ['cnpj', 'gestora', 'master_feeder']
        }

        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor()
        erros = []
        sucessos = 0

        # Valores válidos para campos específicos
        classes_validas = ['Renda Fixa', 'Fundos', 'Prev', 'Listados', 'Cetipados', 'COE', 'Fundos Internacionais', 'Renda Fixa Internacional']
        status_validos = ['Em Analise', 'Aprovado', 'Reprovado']
        perfis_validos = ['Conservador', 'Moderado', 'Sofisticado', 'Todos']
        master_feeder_validos = ['Master', 'Feeder', '']

        for index, row in df.iterrows():
            try:
                # Preparar os dados
                data = row.to_dict()
                
                # Validar classe
                classe = data.get('classe')
                if not classe or classe not in classes_validas:
                    erros.append({
                        'linha': index + 2,
                        'erro': f'Classe inválida: {classe}. Valores válidos: {", ".join(classes_validas)}'
                    })
                    continue

                # Validar campos obrigatórios para a classe
                campos_obrigatorios = campos_obrigatorios_por_classe.get(classe, [])
                campos_vazios = [campo for campo in campos_obrigatorios if not data.get(campo) or str(data.get(campo)).strip() == '']
                if campos_vazios:
                    erros.append({
                        'linha': index + 2,
                        'erro': f'Campos obrigatórios vazios para a classe {classe}: {", ".join(campos_vazios)}'
                    })
                    continue

                # Validar valores específicos
                if data.get('status') not in status_validos:
                    erros.append({
                        'linha': index + 2,
                        'erro': f'Status inválido: {data.get("status")}. Valores válidos: {", ".join(status_validos)}'
                    })
                    continue

                if data.get('perfil') not in perfis_validos:
                    erros.append({
                        'linha': index + 2,
                        'erro': f'Perfil inválido: {data.get("perfil")}. Valores válidos: {", ".join(perfis_validos)}'
                    })
                    continue

                if data.get('master_feeder') not in master_feeder_validos:
                    erros.append({
                        'linha': index + 2,
                        'erro': f'Master/Feeder inválido: {data.get("master_feeder")}. Valores válidos: {", ".join(master_feeder_validos)}'
                    })
                    continue

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
                cnpj = limpar_cnpj(str(data.get('cnpj', '')))

                # Tratar ISIN vazio
                isin = data.get('isin')
                if not isin or str(isin).strip() == '':
                    isin = None

                # Tratar ticker vazio
                ticker = data.get('ticker')
                if not ticker or str(ticker).strip() == '':
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
                    erros.append({
                        'linha': index + 2,
                        'erro': mensagem
                    })
                    continue

                # Calcular prazo restante
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

                # Registrar no histórico
                ativo_id = cursor.lastrowid
                cursor.execute("""
                    INSERT INTO ativo_history (
                        ativo_id, user_id, action_type, changes
                    ) VALUES (
                        %s, %s, 'IMPORT', %s
                    )
                """, (
                    ativo_id,
                    request.user_id,
                    json.dumps(data)
                ))

                sucessos += 1

            except Exception as e:
                erros.append({
                    'linha': index + 2,
                    'erro': str(e)
                })

        connection.commit()
        
        # Limpar arquivo temporário
        os.remove(temp_path)
        os.rmdir(temp_dir)

        total_linhas = len(df)
        total_erros = len(erros)
        total_sucessos = sucessos

        return jsonify({
            'message': f'Importação concluída. Total de linhas: {total_linhas}. Ativos inseridos com sucesso: {total_sucessos}. Ativos com erro: {total_erros}.',
            'erros': erros
        }), 200

    except Exception as e:
        return jsonify({'error': f'Erro ao importar ativos em lote: {str(e)}'}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close() 

@ativos_bp.route('/api/ativos/historico/<int:ativo_id>', methods=['GET'])
@token_required
def consultar_historico(ativo_id, current_user=None):
    try:
        print(f'Tentando consultar histórico para ativo_id: {ativo_id}')
        connection = get_db_connection()
        if connection is None:
            print('Erro: Conexão com o banco falhou')
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Verificar se o ativo existe
        cursor.execute('SELECT id FROM ativos WHERE id = %s', (ativo_id,))
        if not cursor.fetchone():
            print(f'Erro: Ativo com id {ativo_id} não encontrado')
            cursor.close()
            connection.close()
            return jsonify({'error': 'Ativo não encontrado'}), 404
        
        # Consultar histórico do ativo
        print('Executando consulta de histórico')
        cursor.execute("""
            SELECT 
                h.id,
                h.action_type,
                h.action_date,
                h.changes,
                u.username as user_name
            FROM ativo_history h
            JOIN user u ON h.user_id = u.id
            WHERE h.ativo_id = %s
            ORDER BY h.action_date DESC
        """, (ativo_id,))
        
        historico = cursor.fetchall()
        print(f'Histórico encontrado: {len(historico)} registros')
        
        cursor.close()
        connection.close()
        
        return jsonify({'historico': historico}), 200
        
    except Exception as e:
        print(f'Erro ao consultar histórico: {str(e)}')
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': 'Erro ao consultar histórico'}), 500

@ativos_bp.route('/api/ativos', methods=['GET'])
@token_required
def listar_ativos(current_user=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Modificar a query para incluir a última data de ação do histórico
        query = """
            SELECT 
                a.*,
                (SELECT MAX(h.action_date) 
                 FROM ativo_history h 
                 WHERE h.ativo_id = a.id) as action_date
            FROM ativos a
            ORDER BY a.nome
        """
        
        cursor.execute(query)
        ativos = cursor.fetchall()
        
        # Formatar CNPJ em todos os ativos
        for ativo in ativos:
            if 'cnpj' in ativo:
                ativo['cnpj'] = formatar_cnpj(ativo['cnpj'])
        
        cursor.close()
        conn.close()
        
        return jsonify({'ativos': ativos}), 200
        
    except Exception as e:
        print(f'Erro ao listar ativos: {str(e)}')
        return jsonify({'error': 'Erro ao listar ativos'}), 500 

@ativos_bp.route('/api/ativos/classificacoes', methods=['GET'])
@token_required
def listar_classificacoes(current_user=None):
    """Retorna todas as classificações de ativos cadastradas"""
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                c.id, c.ativo_id, c.classe_investimento, c.indexador_primario, 
                c.tipo_indexador, c.data_classificacao
            FROM 
                ativo_classificacao c
        """
        
        cursor.execute(query)
        classificacoes = cursor.fetchall()
        
        # Converter datas para string para serialização JSON
        for classificacao in classificacoes:
            if 'data_classificacao' in classificacao and classificacao['data_classificacao']:
                classificacao['data_classificacao'] = classificacao['data_classificacao'].isoformat()
        
        cursor.close()
        connection.close()
        
        return jsonify({'classificacoes': classificacoes}), 200
    
    except Error as e:
        print(f"Erro SQL ao listar classificações: {str(e)}")
        return jsonify({'error': f'Erro ao buscar classificações: {str(e)}'}), 500

@ativos_bp.route('/api/ativos/classificacao', methods=['POST'])
@token_required
def criar_classificacao(current_user=None):
    """Cria uma nova classificação para um ativo"""
    connection = None
    cursor = None
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem criar classificações'}), 403

        data = request.get_json()
        print(f"Dados recebidos na criação: {data}")
        
        # Validar campos obrigatórios
        campos_obrigatorios = ['ativo_id', 'classe_investimento', 'indexador_primario', 'tipo_indexador']
        for campo in campos_obrigatorios:
            if campo not in data:
                return jsonify({'error': f'Campo obrigatório não fornecido: {campo}'}), 400

        # Validar tipo de indexador
        if data['tipo_indexador'] not in ['cnpj', 'ticker', 'isin']:
            return jsonify({'error': 'Tipo de indexador inválido. Deve ser cnpj, ticker ou isin'}), 400

        # Validar classe de investimento
        classes_investimento_validas = [
            'Pós-Fixado', 'Inflação', 'Pré-Fixado', 'Multimercado',
            'Renda Variável Brasil', 'Fundos Listados', 'Alternativos',
            'Renda Fixa Global', 'Renda Variável Internacional'
        ]
        if data['classe_investimento'] not in classes_investimento_validas:
            return jsonify({'error': f'Classe de investimento inválida. Deve ser uma das seguintes: {", ".join(classes_investimento_validas)}'}), 400

        # Garantir que indexador_primario não seja None
        if data['indexador_primario'] is None:
            data['indexador_primario'] = ''

        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se o ativo existe
        cursor.execute("SELECT id FROM ativos WHERE id = %s", (data['ativo_id'],))
        if not cursor.fetchone():
            return jsonify({'error': 'Ativo não encontrado'}), 404

        # Verificar se já existe uma classificação para este ativo
        cursor.execute("SELECT id FROM ativo_classificacao WHERE ativo_id = %s", (data['ativo_id'],))
        if cursor.fetchone():
            return jsonify({'error': 'Já existe uma classificação para este ativo. Use PUT para atualizar.'}), 409

        # Inserir a classificação
        query = """
            INSERT INTO ativo_classificacao (
                ativo_id, classe_investimento, indexador_primario, tipo_indexador, data_classificacao
            ) VALUES (
                %s, %s, %s, %s, NOW()
            )
        """

        print(f"Executando query de inserção com valores: {data['ativo_id']}, {data['classe_investimento']}, {data['indexador_primario']}, {data['tipo_indexador']}")
        
        cursor.execute(query, (
            data['ativo_id'],
            data['classe_investimento'],
            data['indexador_primario'],
            data['tipo_indexador']
        ))

        classificacao_id = cursor.lastrowid
        
        # Registrar no histórico - Padronizando o formato para ser igual ao de atualização
        cursor.execute("""
            INSERT INTO ativo_history (
                ativo_id, user_id, action_type, changes
            ) VALUES (
                %s, %s, 'CLASSIFICACAO', %s
            )
        """, (
            data['ativo_id'],
            request.user_id,
            json.dumps({
                'anterior': None,
                'novo': data
            })
        ))

        # Confirmar transação
        connection.commit()
        
        # Obter a classificação recém-criada
        cursor.execute("SELECT * FROM ativo_classificacao WHERE id = %s", (classificacao_id,))
        nova_classificacao = cursor.fetchone()
        
        # Converter data para string para serialização JSON
        if nova_classificacao and 'data_classificacao' in nova_classificacao:
            nova_classificacao['data_classificacao'] = nova_classificacao['data_classificacao'].isoformat()
        
        print(f"Classificação criada: {nova_classificacao}")
        
        # Retornar a classificação criada
        return jsonify({
            'message': 'Classificação criada com sucesso',
            'classificacao': nova_classificacao
        }), 201
            
    except Error as e:
        if connection:
            connection.rollback()
        print(f"Erro SQL ao criar classificação: {str(e)}")
        return jsonify({'error': f'Erro ao criar classificação: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

@ativos_bp.route('/api/ativos/classificacao/<int:id>', methods=['PUT'])
@token_required
def atualizar_classificacao(id, current_user=None):
    """Atualiza uma classificação existente"""
    connection = None
    cursor = None
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem atualizar classificações'}), 403

        data = request.get_json()
        print(f"Dados recebidos na atualização: {data}")
        
        # Validar campos obrigatórios
        campos_obrigatorios = ['ativo_id', 'classe_investimento', 'indexador_primario', 'tipo_indexador']
        for campo in campos_obrigatorios:
            if campo not in data:
                return jsonify({'error': f'Campo obrigatório não fornecido: {campo}'}), 400

        # Validar tipo de indexador
        if data['tipo_indexador'] not in ['cnpj', 'ticker', 'isin']:
            return jsonify({'error': 'Tipo de indexador inválido. Deve ser cnpj, ticker ou isin'}), 400

        # Validar classe de investimento
        classes_investimento_validas = [
            'Pós-Fixado', 'Inflação', 'Pré-Fixado', 'Multimercado',
            'Renda Variável Brasil', 'Fundos Listados', 'Alternativos',
            'Renda Fixa Global', 'Renda Variável Internacional'
        ]
        if data['classe_investimento'] not in classes_investimento_validas:
            return jsonify({'error': f'Classe de investimento inválida. Deve ser uma das seguintes: {", ".join(classes_investimento_validas)}'}), 400

        # Garantir que indexador_primario não seja None
        if data['indexador_primario'] is None:
            data['indexador_primario'] = ''

        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se a classificação existe
        cursor.execute("SELECT * FROM ativo_classificacao WHERE id = %s", (id,))
        classificacao_atual = cursor.fetchone()
        if not classificacao_atual:
            return jsonify({'error': 'Classificação não encontrada'}), 404

        # Verificar se o ativo existe
        cursor.execute("SELECT id FROM ativos WHERE id = %s", (data['ativo_id'],))
        if not cursor.fetchone():
            return jsonify({'error': 'Ativo não encontrado'}), 404

        # Atualizar a classificação
        query = """
            UPDATE ativo_classificacao 
            SET classe_investimento = %s,
                indexador_primario = %s, 
                tipo_indexador = %s, 
                data_classificacao = NOW()
            WHERE id = %s
        """

        print(f"Executando query de atualização com valores: {data['classe_investimento']}, {data['indexador_primario']}, {data['tipo_indexador']}, {id}")
        
        cursor.execute(query, (
            data['classe_investimento'],
            data['indexador_primario'],
            data['tipo_indexador'],
            id
        ))

        # Registrar no histórico
        cursor.execute("""
            INSERT INTO ativo_history (
                ativo_id, user_id, action_type, changes
            ) VALUES (
                %s, %s, 'UPDATE_CLASSIFICACAO', %s
            )
        """, (
            data['ativo_id'],
            request.user_id,
            json.dumps({
                'anterior': {k: str(v) if isinstance(v, datetime) else v for k, v in classificacao_atual.items()},
                'novo': data
            })
        ))

        # Confirmar transação
        connection.commit()

        # Buscar a classificação atualizada
        cursor.execute("SELECT * FROM ativo_classificacao WHERE id = %s", (id,))
        classificacao_atualizada = cursor.fetchone()
        
        # Converter data para string para serialização JSON
        if classificacao_atualizada and 'data_classificacao' in classificacao_atualizada:
            classificacao_atualizada['data_classificacao'] = classificacao_atualizada['data_classificacao'].isoformat()
        
        print(f"Classificação atualizada: {classificacao_atualizada}")
        
        return jsonify({
            'message': 'Classificação atualizada com sucesso',
            'classificacao': classificacao_atualizada
        }), 200
            
    except Error as e:
        if connection:
            connection.rollback()
        print(f"Erro SQL ao atualizar classificação: {str(e)}")
        return jsonify({'error': f'Erro ao atualizar classificação: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

@ativos_bp.route('/api/ativos/classificacao/<int:id>', methods=['DELETE'])
@token_required
def excluir_classificacao(id, current_user=None):
    """Exclui uma classificação existente"""
    try:
        # Verificar se o usuário é Admin ou Research
        if request.user_role not in ['Admin', 'Research']:
            return jsonify({'error': 'Acesso negado: somente Admin e Research podem excluir classificações'}), 403

        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)

        # Verificar se a classificação existe
        cursor.execute("SELECT * FROM ativo_classificacao WHERE id = %s", (id,))
        classificacao = cursor.fetchone()
        if not classificacao:
            return jsonify({'error': 'Classificação não encontrada'}), 404

        # Registrar no histórico antes de excluir
        cursor.execute("""
            INSERT INTO ativo_history (
                ativo_id, user_id, action_type, changes
            ) VALUES (
                %s, %s, 'DELETE_CLASSIFICACAO', %s
            )
        """, (
            classificacao['ativo_id'],
            request.user_id,
            json.dumps({
                'anterior': {k: str(v) if isinstance(v, datetime) else v for k, v in classificacao.items()},
                'novo': None
            })
        ))

        # Excluir a classificação
        cursor.execute("DELETE FROM ativo_classificacao WHERE id = %s", (id,))

        connection.commit()
        cursor.close()
        connection.close()

        return jsonify({
            'message': 'Classificação excluída com sucesso'
        }), 200
    
    except Error as e:
        return jsonify({'error': f'Erro ao excluir classificação: {str(e)}'}), 500 

@ativos_bp.route('/api/ativos/historico/resumo', methods=['GET'])
@token_required
def obter_resumo_historico(current_user=None):
    """
    Retorna um resumo das atividades diárias de ativos, agrupadas por tipo de ação.
    Parâmetros:
    - date: Data no formato YYYY-MM-DD para filtrar as atividades (opcional)
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Obter a data do parâmetro de consulta ou usar a data atual
        data = request.args.get('date')
        
        # Construir a consulta base
        query = """
            SELECT 
                action_type,
                COUNT(*) as quantidade
            FROM 
                ativo_history
        """
        
        params = []
        
        # Adicionar filtro por data se especificado
        if data:
            query += " WHERE DATE(action_date) = %s"
            params.append(data)
            
        # Agrupar por tipo de ação
        query += " GROUP BY action_type"
        
        cursor.execute(query, params)
        resultados = cursor.fetchall()
        
        # Inicializar o resumo com zeros
        resumo = {
            'classificacoes': 0,
            'atualizacoes_classificacao': 0,
            'importacoes': 0,
            'atualizacoes': 0,
            'total': 0
        }
        
        # Preencher o resumo com os resultados da consulta
        for resultado in resultados:
            if resultado['action_type'] == 'CLASSIFICACAO':
                resumo['classificacoes'] = resultado['quantidade']
            elif resultado['action_type'] == 'UPDATE_CLASSIFICACAO':
                resumo['atualizacoes_classificacao'] = resultado['quantidade']
            elif resultado['action_type'] == 'IMPORT':
                resumo['importacoes'] = resultado['quantidade']
            elif resultado['action_type'] == 'UPDATE':
                resumo['atualizacoes'] = resultado['quantidade']
            
            resumo['total'] += resultado['quantidade']
        
        cursor.close()
        connection.close()
        
        return jsonify({'resumo': resumo}), 200
        
    except Exception as e:
        print(f'Erro ao obter resumo do histórico: {str(e)}')
        return jsonify({'error': 'Erro ao obter resumo do histórico'}), 500 