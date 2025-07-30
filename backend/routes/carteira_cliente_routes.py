from flask import Blueprint, request, jsonify
from middleware.auth import token_required
from utils.db import get_db_connection
from mysql.connector import Error
import json
from datetime import datetime, timedelta
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Configurar backend não-GUI para evitar warnings
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import io
import base64
import tempfile
import os

carteira_cliente_bp = Blueprint('carteira_cliente', __name__)

@carteira_cliente_bp.route('/api/carteira-cliente/clientes', methods=['GET'])
@token_required
def get_clientes(current_user=None):
    """
    Retorna a lista de clientes disponíveis para o usuário atual.
    Se o usuário for Admin, pode optar por ver todos os clientes.
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Verificar se o parâmetro "todos" está presente e se o usuário é Admin
        todos = request.args.get('todos', 'false').lower() == 'true'
        
        if todos and request.user_role == 'Admin':
            # Admin solicitando todos os clientes
            query = """
                SELECT c.id, c.client_name, c.risk_profile, c.score, u.username as consultor
                FROM client c
                JOIN user u ON c.user_id = u.id
                ORDER BY c.client_name
            """
            cursor.execute(query)
        else:
            # Usuário solicitando apenas seus clientes
            query = """
                SELECT c.id, c.client_name, c.risk_profile, c.score, u.username as consultor
                FROM client c
                JOIN user u ON c.user_id = u.id
                WHERE c.user_id = %s
                ORDER BY c.client_name
            """
            cursor.execute(query, (request.user_id,))
        
        clientes = cursor.fetchall()
        
        return jsonify({'clientes': clientes}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar clientes: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@carteira_cliente_bp.route('/api/carteira-cliente/meses-disponiveis', methods=['GET'])
@token_required
def get_meses_disponiveis(current_user=None):
    """
    Retorna a lista de meses que possuem carteiras recomendadas cadastradas.
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT DISTINCT mes_referencia 
            FROM carteira_recomendada 
            ORDER BY mes_referencia DESC
        """
        cursor.execute(query)
        
        meses = [row['mes_referencia'] for row in cursor.fetchall()]
        
        return jsonify({'meses': meses}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar meses disponíveis: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

def calcular_perfil_ponderado(score, perfil_atual):
    """
    Calcula o perfil ponderado baseado no score do cliente.
    Score baixo = mais conservador, Score alto = mais sofisticado
    """
    # Definir ranges de score para cada perfil
    ranges = {
        'Conservador': (0, 30),
        'Moderado': (31, 70),
        'Sofisticado': (71, 100)
    }
    
    # Determinar perfil baseado no score
    if score <= 30:
        perfil_score = 'Conservador'
        peso_atual = 0.8
        peso_adjacente = 0.2
    elif score <= 70:
        perfil_score = 'Moderado'
        peso_atual = 0.7
        peso_adjacente = 0.3
    else:
        perfil_score = 'Sofisticado'
        peso_atual = 0.8
        peso_adjacente = 0.2
    
    # Se o perfil do score é diferente do perfil atual, ajustar pesos
    if perfil_score != perfil_atual:
        if perfil_atual == 'Moderado':
            peso_atual = 0.6
            peso_adjacente = 0.4
        else:
            peso_atual = 0.7
            peso_adjacente = 0.3
    
    return {
        'perfil_principal': perfil_atual,
        'perfil_secundario': perfil_score,
        'peso_principal': peso_atual,
        'peso_secundario': peso_adjacente
    }

def obter_ativos_por_classe(cursor, classe_investimento):
    """
    Busca ativos aprovados por classe de investimento
    """
    query = """
        SELECT a.id, a.nome, a.classe, a.ticker, a.isin, a.cnpj, a.gestora
        FROM ativos a
        JOIN ativo_classificacao ac ON a.id = ac.ativo_id
        WHERE ac.classe_investimento = %s 
        AND a.status = 'Aprovado'
        ORDER BY a.nome
    """
    cursor.execute(query, (classe_investimento,))
    return cursor.fetchall()

def obter_ativo_generico_por_classe(classe_investimento):
    """
    Retorna um ativo genérico para cada classe quando não há ativos específicos
    """
    ativos_genericos = {
        'Pós-Fixado': {
            'nome': 'CDI (Ativo Direto)',
            'ticker': 'CDI',
            'tipo': 'Ativo Direto Pós-Fixado'
        },
        'Inflação': {
            'nome': 'ANBIMA IMAB (Ativo Direto)',
            'ticker': 'ANBIMA_IMAB',
            'tipo': 'Ativo Direto Inflação'
        },
        'Pré-Fixado': {
            'nome': 'ANBIMA IRFM (Ativo Direto)',
            'ticker': 'ANBIMA_IRFM',
            'tipo': 'Ativo Direto Pré-Fixado'
        },
        'Multimercado': {
            'nome': 'ANBIMA IHFA (Ativo Direto)',
            'ticker': 'ANBIMA_IHFA',
            'tipo': 'Ativo Direto Multimercado'
        },
        'Renda Variável Brasil': {
            'nome': 'IBOV (Ativo Direto)',
            'ticker': 'IBOV',
            'tipo': 'Ativo Direto Renda Variável'
        },
        'Fundos Listados': {
            'nome': 'IFIX (Ativo Direto)',
            'ticker': 'IFIX',
            'tipo': 'Ativo Direto ETF'
        },
        'Alternativos': {
            'nome': 'Fundo Alternativo (Ativo Direto)',
            'ticker': 'Alternativo',
            'tipo': 'Ativo Direto Alternativo'
        },
        'Renda Fixa Global': {
            'nome': 'Bloomberg US Aggregate (Ativo Direto)',
            'ticker': 'Bloomberg_US_Aggregate',
            'tipo': 'Ativo Direto Renda Fixa Global'
        },
        'Renda Variável Internacional': {
            'nome': 'S&P 500 (Ativo Direto)',
            'ticker': 'US:SP500',
            'tipo': 'Ativo Direto Renda Variável Internacional'
        }
    }
    
    return ativos_genericos.get(classe_investimento, {
        'nome': f'{classe_investimento} (Ativo Direto)',
        'ticker': classe_investimento,
        'tipo': f'Ativo Direto {classe_investimento}'
    })

def otimizar_alocacao(bandas, nota_qualitativa, perfil_ponderado):
    """
    Otimiza a alocação baseada nas bandas, nota qualitativa e perfil ponderado
    """
    # Calcular alocação base nas bandas
    alocacao_base = (bandas['banda_inferior'] + bandas['banda_neutra'] + bandas['banda_superior']) / 3
    
    # Ajustar baseado na nota qualitativa (-2 a +2)
    ajuste_nota = nota_qualitativa * 0.05  # 5% de ajuste por ponto
    alocacao_ajustada = alocacao_base * (1 + ajuste_nota)
    
    # Ajustar baseado no perfil ponderado
    if perfil_ponderado['perfil_principal'] != perfil_ponderado['perfil_secundario']:
        # Se há diferença entre perfis, ajustar para o perfil mais conservador
        if perfil_ponderado['perfil_principal'] == 'Conservador':
            alocacao_ajustada *= 0.9  # Reduzir alocação em classes mais arriscadas
        elif perfil_ponderado['perfil_principal'] == 'Sofisticado':
            alocacao_ajustada *= 1.1  # Aumentar alocação em classes mais arriscadas
    
    # Garantir que a alocação esteja dentro das bandas
    alocacao_final = max(bandas['banda_inferior'], min(bandas['banda_superior'], alocacao_ajustada))
    
    return round(alocacao_final, 2)

def verificar_modificacoes_ativos(cursor, carteira_existente, mes_referencia):
    """
    Verifica se houve modificações nos ativos que justifiquem regenerar a carteira
    """
    try:
        # Extrair IDs dos ativos da carteira existente
        ativos_ids = []
        for ativo in carteira_existente['carteira_otimizada']:
            if ativo.get('ativo_id'):
                ativos_ids.append(ativo['ativo_id'])
        
        if not ativos_ids:
            return False, "Carteira não possui ativos específicos"
        
        # Verificar se algum ativo foi reprovado ou teve status alterado
        cursor.execute("""
            SELECT id, status, nome 
            FROM ativos 
            WHERE id IN (%s)
        """ % ','.join(['%s'] * len(ativos_ids)), tuple(ativos_ids))
        
        ativos_atuais = cursor.fetchall()
        
        # Verificar se algum ativo foi reprovado
        ativos_reprovados = [ativo for ativo in ativos_atuais if ativo['status'] == 'Reprovado']
        
        if ativos_reprovados:
            return True, f"Ativos reprovados encontrados: {', '.join([a['nome'] for a in ativos_reprovados])}"
        
        # Verificar se houve mudanças nas avaliações qualitativas
        cursor.execute("""
            SELECT classe_ativo, nota
            FROM avaliacao_classe_ativo
            WHERE mes_referencia = %s
        """, (mes_referencia,))
        
        avaliacoes_atuais = {row['classe_ativo']: row['nota'] for row in cursor.fetchall()}
        avaliacoes_anteriores = carteira_existente.get('notas_qualitativas', {})
        
        # Verificar se houve mudanças significativas nas avaliações
        mudancas_avaliacao = []
        for classe, nota_atual in avaliacoes_atuais.items():
            nota_anterior = avaliacoes_anteriores.get(classe)
            if nota_anterior is not None and nota_atual != nota_anterior:
                mudancas_avaliacao.append(f"{classe}: {nota_anterior} → {nota_atual}")
        
        if mudancas_avaliacao:
            return True, f"Mudanças nas avaliações qualitativas: {'; '.join(mudancas_avaliacao)}"
        
        return False, "Nenhuma modificação relevante encontrada"
        
    except Exception as e:
        print(f"Erro ao verificar modificações: {str(e)}")
        return True, "Erro ao verificar modificações - regenerando por segurança"

def gerar_explicacao_metodologia(carteira_cliente, carteira_recomendada, avaliacoes):
    """
    Gera explicações detalhadas da metodologia e justificativas para cada ativo
    """
    # Verificar se os parâmetros necessários estão presentes
    if not carteira_cliente or not carteira_recomendada:
        return {
            'metodologia_geral': {
                'titulo': 'Metodologia de Otimização de Carteira',
                'passos': [
                    '1. Análise do perfil de risco do cliente e score de suitability',
                    '2. Cálculo do perfil ponderado combinando perfil atual e score',
                    '3. Aplicação das notas qualitativas por classe de ativo',
                    '4. Otimização da alocação dentro das bandas recomendadas',
                    '5. Seleção quantitativa de ativos baseada em métricas financeiras',
                    '6. Normalização final para totalizar 100%'
                ]
            },
            'perfil_ponderado': {
                'titulo': 'Cálculo do Perfil Ponderado',
                'explicacao': 'Informações do perfil ponderado não disponíveis no momento.'
            },
            'otimizacao_alocacao': {
                'titulo': 'Processo de Otimização de Alocação',
                'explicacao': 'Informações de otimização não disponíveis no momento.'
            },
            'selecao_quantitativa': {
                'titulo': 'Seleção Quantitativa de Ativos',
                'explicacao': 'Informações de seleção quantitativa não disponíveis no momento.'
            },
            'justificativas_ativos': []
        }
    
    explicacoes = {
        'metodologia_geral': {
            'titulo': 'Metodologia de Otimização de Carteira',
            'passos': [
                '1. Análise do perfil de risco do cliente e score de suitability',
                '2. Cálculo do perfil ponderado combinando perfil atual e score',
                '3. Aplicação das notas qualitativas por classe de ativo',
                '4. Otimização da alocação dentro das bandas recomendadas',
                '5. Seleção quantitativa de ativos baseada em métricas financeiras',
                '6. Normalização final para totalizar 100%'
            ]
        },
        'perfil_ponderado': {
            'titulo': 'Cálculo do Perfil Ponderado',
            'explicacao': f"""
            O perfil ponderado combina o perfil de risco atual ({carteira_cliente.get('perfil_risco', 'N/A')}) 
            com o score de suitability ({carteira_cliente.get('score_suitability', 'N/A')}).
            
            • Perfil Principal: {carteira_cliente.get('perfil_ponderado', {}).get('perfil_principal', 'N/A')} 
              (Peso: {carteira_cliente.get('perfil_ponderado', {}).get('peso_principal', 0) * 100}%)
            • Perfil Secundário: {carteira_cliente.get('perfil_ponderado', {}).get('perfil_secundario', 'N/A')} 
              (Peso: {carteira_cliente.get('perfil_ponderado', {}).get('peso_secundario', 0) * 100}%)
            
            Esta ponderação ajusta a alocação para refletir tanto a tolerância ao risco 
            declarada quanto a sofisticação financeira medida pelo score.
            """
        },
        'otimizacao_alocacao': {
            'titulo': 'Processo de Otimização de Alocação',
            'explicacao': """
            Para cada classe de ativo, a alocação é calculada seguindo estes passos:
            
            1. Alocação base: média das bandas (inferior + neutra + superior) / 3
            2. Ajuste por nota qualitativa: ±5% por ponto da nota (-2 a +2)
            3. Ajuste por perfil ponderado: ±10% baseado na diferença entre perfis
            4. Limitação às bandas: garantia de que a alocação final está dentro dos limites
            """
        },
        'selecao_quantitativa': {
            'titulo': 'Seleção Quantitativa e Distribuição por Perfil',
            'explicacao': f"""
            A seleção e distribuição de ativos é realizada através de análise quantitativa rigorosa
            combinada com estratégias específicas para o perfil {carteira_cliente.get('perfil_risco', 'Moderado')}:
            
            📊 Métricas Calculadas:
            • Sharpe Ratio: Retorno ajustado ao risco (meta: > 0.5)
            • Retorno Anualizado: Performance histórica anualizada
            • Volatilidade Anualizada: Variabilidade dos retornos (menor = melhor)
            • Máximo Drawdown: Maior perda histórica (menor = melhor)
            • Beta: Sensibilidade ao mercado (próximo a 1.0 = ideal)
            • VaR 95%: Perda máxima esperada com 95% de confiança
            • CVaR 95%: Perda média nos piores 5% dos cenários
            
            🎯 Score Quantitativo por Classe:
            Cada ativo recebe um score baseado em pesos específicos por classe:
            • Renda Variável: Maior peso no retorno (35%) e Sharpe (25%)
            • Renda Fixa: Maior peso na volatilidade (25%) e Sharpe (35%)
            • Alternativos: Peso equilibrado entre todos os fatores
            
            🎭 Estratégia de Distribuição por Perfil:
            {gerar_explicacao_estrategia_perfil(carteira_cliente.get('perfil_risco', 'Moderado'))}
            
            📈 Seleção e Distribuição Final:
            Os ativos são selecionados e distribuídos considerando tanto o score quantitativo
            quanto as preferências de risco do perfil do cliente, garantindo uma carteira
            personalizada e otimizada.
            """
        },
        'justificativas_ativos': []
    }
    
    # Gerar justificativas para cada ativo
    if carteira_cliente.get('carteira_otimizada'):
        for ativo in carteira_cliente['carteira_otimizada']:
            classe_ativo = ativo.get('classe_ativo', 'N/A')
            
            # Encontrar dados da classe na carteira recomendada
            classe_recomendada = next((c for c in carteira_recomendada if c.get('classe_ativo') == classe_ativo), None)
            nota_qualitativa = avaliacoes.get(classe_ativo, 0)
            
            # Calcular alocação base para comparação
            if classe_recomendada:
                alocacao_base = (classe_recomendada.get('banda_inferior', 0) + classe_recomendada.get('banda_neutra', 0) + classe_recomendada.get('banda_superior', 0)) / 3
                ajuste_nota = nota_qualitativa * 0.05
                alocacao_ajustada = alocacao_base * (1 + ajuste_nota)
            else:
                alocacao_base = 0
                alocacao_ajustada = 0
            
            justificativa = {
                'classe_ativo': classe_ativo,
                'ativo_nome': ativo.get('ativo_nome', 'N/A'),
                'alocacao_final': ativo.get('alocacao', 0),
                'tipo_ativo': ativo.get('tipo', 'N/A'),
                'detalhes': {
                    'alocacao_base': round(alocacao_base, 2) if classe_recomendada else 'N/A',
                    'nota_qualitativa': nota_qualitativa,
                    'ajuste_nota': f"{ajuste_nota * 100:+.1f}%" if classe_recomendada else 'N/A',
                    'alocacao_ajustada': round(alocacao_ajustada, 2) if classe_recomendada else 'N/A',
                    'bandas_recomendadas': {
                        'inferior': classe_recomendada.get('banda_inferior', 'N/A') if classe_recomendada else 'N/A',
                        'neutra': classe_recomendada.get('banda_neutra', 'N/A') if classe_recomendada else 'N/A',
                        'superior': classe_recomendada.get('banda_superior', 'N/A') if classe_recomendada else 'N/A'
                    }
                },
                'justificativa_escolha': gerar_justificativa_escolha_ativo(ativo, nota_qualitativa, classe_recomendada, carteira_cliente.get('perfil_risco'))
            }
            
            explicacoes['justificativas_ativos'].append(justificativa)
    
    return explicacoes

def gerar_justificativa_escolha_ativo(ativo, nota_qualitativa, classe_recomendada, perfil_risco=None):
    """
    Gera justificativa específica para a escolha de cada ativo
    """
    # Verificar se o ativo tem as propriedades necessárias
    if not ativo:
        return "Informações do ativo não disponíveis."
    
    tipo_ativo = ativo.get('tipo', '')
    classe_ativo = ativo.get('classe_ativo', 'N/A')
    alocacao_ativo = ativo.get('alocacao', 0)
    
    if 'Direto' in tipo_ativo:
        # Ativo genérico
        return f"""
        Ativo genérico escolhido para {classe_ativo} devido à ausência de ativos específicos 
        aprovados nesta classe. O ativo genérico representa a exposição direta ao mercado 
        sem intermediários, oferecendo maior transparência e controle sobre a alocação.
        
        • Vantagens: Transparência total, baixo custo, controle direto
        • Considerações: Requer conhecimento técnico para gestão ativa
        """
    else:
        # Ativo específico
        ativo_nome = ativo.get('ativo_nome', 'N/A')
        gestora = ativo.get('gestora', 'N/A')
        ticker = ativo.get('ticker', '')
        isin = ativo.get('isin', '')
        
        justificativa = f"""
        Ativo específico selecionado: {ativo_nome}
        
        • Gestora: {gestora}
        • Identificação: {ticker or isin or 'N/A'}
        • Alocação: {alocacao_ativo}%
        
        Este ativo foi escolhido através de análise quantitativa rigorosa, considerando múltiplas 
        métricas financeiras para otimizar a relação risco-retorno na classe {classe_ativo}.
        """
        
        # Adicionar informações sobre distribuição baseada no perfil
        if perfil_risco:
            estrategias_perfil = {
                'Conservador': {
                    'max_ativos': 2,
                    'concentracao': '70% no principal ativo',
                    'foco': 'preservação de capital e estabilidade'
                },
                'Moderado': {
                    'max_ativos': 3,
                    'concentracao': '50% no principal ativo',
                    'foco': 'crescimento com controle de risco'
                },
                'Sofisticado': {
                    'max_ativos': 4,
                    'concentracao': '40% no principal ativo',
                    'foco': 'crescimento agressivo com gestão de risco'
                }
            }
            
            estrategia = estrategias_perfil.get(perfil_risco, estrategias_perfil['Moderado'])
            
            justificativa += f"""
            
        🎭 Estratégia de Distribuição ({perfil_risco}):
        • Máximo de {estrategia['max_ativos']} ativos por classe
        • Concentração: {estrategia['concentracao']}
        • Foco: {estrategia['foco']}
        """
        
        # Adicionar métricas quantitativas se disponíveis
        if ativo.get('metricas'):
            metricas = ativo['metricas']
            score_quantitativo = ativo.get('score_quantitativo', 0)
            
            justificativa += f"""
            
        📊 Métricas Quantitativas:
        • Sharpe Ratio: {metricas.get('sharpe_ratio', 0):.3f} (meta: > 0.5)
        • Retorno Anualizado: {metricas.get('retorno_anualizado', 0):.2%}
        • Volatilidade Anualizada: {metricas.get('volatilidade_anualizada', 0):.2%}
        • Máximo Drawdown: {metricas.get('max_drawdown', 0):.2%}
        • Beta: {metricas.get('beta', 0):.2f} (meta: próximo a 1.0)
        • Score Quantitativo: {score_quantitativo:.3f}
        
        🎯 Critérios de Seleção:
        • Sharpe Ratio: Mede o retorno ajustado ao risco (quanto maior, melhor)
        • Volatilidade: Mede a variabilidade dos retornos (quanto menor, melhor)
        • Drawdown: Mede a maior perda histórica (quanto menor, melhor)
        • Beta: Mede a sensibilidade ao mercado (próximo a 1.0 é ideal)
        """
        
        # Adicionar contexto baseado na nota qualitativa
        if nota_qualitativa > 0:
            justificativa += f"""
            
        A classe {classe_ativo} recebeu nota qualitativa positiva ({nota_qualitativa}), 
        reforçando a escolha quantitativa e indicando que o momento atual é favorável 
        para esta exposição.
        """
        elif nota_qualitativa < 0:
            justificativa += f"""
            
        A classe {classe_ativo} recebeu nota qualitativa negativa ({nota_qualitativa}), 
        mas a análise quantitativa demonstrou que este ativo específico apresenta 
        características superiores dentro da classe, justificando sua inclusão 
        mesmo em um cenário desfavorável para a classe como um todo.
        """
        
        return justificativa

def buscar_serie_historica_ativo(cursor, ativo, periodo_dias=365):
    """
    Busca a série histórica do ativo na tabela dados_cmd considerando ticker, cnpj, isin e prefixos
    Retorna apenas dados do período especificado (padrão: 1 ano)
    """
    identificadores = []
    # Ticker
    if ativo.get('ticker'):
        identificadores.append(ativo['ticker'])
    # CNPJ - tentar com e sem formatação
    if ativo.get('cnpj'):
        cnpj = ativo['cnpj']
        # Adicionar CNPJ sem formatação
        identificadores.append(cnpj)
        # Adicionar CNPJ formatado se não estiver formatado
        if len(cnpj) == 14 and cnpj.isdigit():
            cnpj_formatado = f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"
            identificadores.append(cnpj_formatado)
    # ISIN
    if ativo.get('isin'):
        identificadores.append(ativo['isin'])
    # Para debêntures e CRIs/CRAs, tentar prefixos
    if ativo.get('ticker'):
        ticker = ativo['ticker']
        if ticker.startswith('CRI') or ticker.startswith('CRA') or ticker.startswith('CDCA'):
            identificadores.append(f'CETIP_{ticker}')
        if ticker.startswith('DEB'):
            identificadores.append(f'DEB:{ticker}')
    if ativo.get('isin'):
        isin = ativo['isin']
        if isin and (isin.startswith('CRI') or isin.startswith('CRA') or isin.startswith('CDCA')):
            identificadores.append(f'CETIP_{isin}')
        if isin and isin.startswith('DEB'):
            identificadores.append(f'DEB:{isin}')
    
    # Calcular data limite baseada no período especificado
    data_atual = datetime.now()
    data_limite = data_atual - timedelta(days=periodo_dias)
    
    # Buscar na tabela dados_cmd
    for identificador in identificadores:
        cursor.execute("SELECT dados FROM dados_cmd WHERE ativo = %s", (identificador,))
        row = cursor.fetchone()
        if row:
            try:
                dados_json = json.loads(row['dados'])
                serie_dados = dados_json.get('dados', [])
                # Converter para formato esperado e filtrar por data
                serie_historica = []
                for item in serie_dados:
                    if 'data' in item and 'valor' in item and item['valor'] is not None:
                        try:
                            # Tentar diferentes formatos de data
                            if '/' in item['data']:
                                data = datetime.strptime(item['data'], '%d/%m/%Y')
                            else:
                                data = datetime.strptime(item['data'], '%Y-%m-%d')
                            
                            # Filtrar apenas dados do período especificado
                            if data >= data_limite:
                                serie_historica.append({
                                    'data': item['data'],
                                    'valor': float(item['valor'])
                                })
                        except ValueError:
                            continue
                
                if len(serie_historica) >= 30:
                    return serie_historica
            except Exception as e:
                print(f"Erro ao processar dados do ativo {identificador}: {str(e)}")
    return []

def calcular_metricas_quantitativas(cursor, classe_ativo, mes_referencia, limite_ativos=3, periodo_dias=365):
    """
    Calcula métricas quantitativas para ativos de uma classe e retorna apenas os que possuem métricas válidas
    """
    try:
        # Buscar ativos aprovados da classe
        cursor.execute("""
            SELECT a.id, a.nome, a.ticker, a.isin, a.cnpj, a.gestora
            FROM ativos a
            JOIN ativo_classificacao ac ON a.id = ac.ativo_id
            WHERE ac.classe_investimento = %s 
            AND a.status = 'Aprovado'
        """, (classe_ativo,))
        ativos_disponiveis = cursor.fetchall()
        if not ativos_disponiveis:
            print(f"[DEBUG] Classe {classe_ativo}: Nenhum ativo aprovado encontrado")
            return []
        
        ativos_com_metricas = []
        ativos_rejeitados = []
        
        # Calcular data limite baseada no período especificado
        from datetime import datetime, timedelta
        data_atual = datetime.now()
        data_limite = data_atual - timedelta(days=periodo_dias)
        
        print(f"[DEBUG] Classe {classe_ativo}: Analisando {len(ativos_disponiveis)} ativos aprovados (período: {periodo_dias} dias)")
        
        for ativo in ativos_disponiveis:
            serie_historica = buscar_serie_historica_ativo(cursor, ativo, periodo_dias)
            if len(serie_historica) >= 30:
                # Verificar se o ativo tem dados do período especificado
                data_mais_recente = None
                try:
                    # Tentar extrair a data mais recente da série
                    datas = []
                    for item in serie_historica:
                        if 'data' in item:
                            try:
                                # Tentar diferentes formatos de data
                                if '/' in item['data']:
                                    data = datetime.strptime(item['data'], '%d/%m/%Y')
                                else:
                                    data = datetime.strptime(item['data'], '%Y-%m-%d')
                                datas.append(data)
                            except ValueError:
                                continue
                    
                    if datas:
                        data_mais_recente = max(datas)
                        
                        # Verificar se o ativo tem dados do período especificado
                        if data_mais_recente < data_limite:
                            print(f"[DEBUG] Ativo {ativo['nome']} ignorado - dados muito antigos. Última data: {data_mais_recente.strftime('%d/%m/%Y')}")
                            ativos_rejeitados.append(f"{ativo['nome']} - dados antigos")
                            continue
                        
                        print(f"[DEBUG] Ativo {ativo['nome']} - última data: {data_mais_recente.strftime('%d/%m/%Y')} - OK")
                    else:
                        print(f"[DEBUG] Ativo {ativo['nome']} ignorado - não foi possível extrair datas válidas")
                        ativos_rejeitados.append(f"{ativo['nome']} - datas inválidas")
                        continue
                        
                except Exception as e:
                    print(f"[DEBUG] Erro ao verificar data do ativo {ativo['nome']}: {str(e)}")
                    ativos_rejeitados.append(f"{ativo['nome']} - erro na data")
                    continue
                
                metricas = calcular_metricas_ativo(serie_historica)
                if metricas and metricas.get('retorno_anualizado') is not None:
                    # Verificar se todas as métricas essenciais estão presentes
                    metricas_essenciais = ['retorno_anualizado', 'volatilidade_anualizada', 'sharpe_ratio', 'max_drawdown']
                    if all(metricas.get(metrica) is not None for metrica in metricas_essenciais):
                        ativos_com_metricas.append({
                            **ativo,
                            'metricas': metricas,
                            'score_quantitativo': calcular_score_quantitativo(metricas, classe_ativo),
                            'data_mais_recente': data_mais_recente.strftime('%d/%m/%Y') if data_mais_recente else None
                        })
                        print(f"[DEBUG] Ativo {ativo['nome']} - métricas calculadas com sucesso")
                    else:
                        print(f"[DEBUG] Ativo {ativo['nome']} ignorado - métricas incompletas")
                        ativos_rejeitados.append(f"{ativo['nome']} - métricas incompletas")
                else:
                    print(f"[DEBUG] Ativo {ativo['nome']} ignorado - não foi possível calcular métricas")
                    ativos_rejeitados.append(f"{ativo['nome']} - erro no cálculo")
            else:
                print(f"[DEBUG] Ativo {ativo['nome']} ignorado - série histórica insuficiente ({len(serie_historica)} pontos)")
                ativos_rejeitados.append(f"{ativo['nome']} - série insuficiente")
        
        # Ordenar ativos com métricas por score
        ativos_com_metricas.sort(key=lambda x: x['score_quantitativo'], reverse=True)
        
        # Retornar apenas ativos com métricas válidas
        resultado = ativos_com_metricas[:limite_ativos]
        
        print(f"[DEBUG] Classe {classe_ativo}: {len(ativos_disponiveis)} ativos analisados")
        print(f"[DEBUG] Classe {classe_ativo}: {len(ativos_com_metricas)} ativos com métricas válidas")
        print(f"[DEBUG] Classe {classe_ativo}: {len(resultado)} ativos selecionados")
        if ativos_rejeitados:
            print(f"[DEBUG] Classe {classe_ativo}: Ativos rejeitados: {', '.join(ativos_rejeitados[:5])}{'...' if len(ativos_rejeitados) > 5 else ''}")
        
        return resultado
    except Exception as e:
        print(f"Erro ao calcular métricas quantitativas para {classe_ativo}: {str(e)}")
        return []

def calcular_metricas_carteira_completa(cursor, carteira_otimizada, mes_referencia, periodo_dias=365):
    """
    Calcula métricas completas para todos os ativos da carteira
    """
    carteira_com_metricas = []
    for ativo in carteira_otimizada:
        ativo_com_metricas = {**ativo}
        if ativo.get('ativo_id'):  # Ativo específico
            serie_historica = buscar_serie_historica_ativo(cursor, ativo, periodo_dias)
            if len(serie_historica) >= 30:
                metricas = calcular_metricas_ativo(serie_historica)
                if metricas:
                    ativo_com_metricas['metricas'] = metricas
                    ativo_com_metricas['score_quantitativo'] = calcular_score_quantitativo(metricas, ativo['classe_ativo'])
                else:
                    ativo_com_metricas['metricas'] = None
                    ativo_com_metricas['score_quantitativo'] = 0
            else:
                ativo_com_metricas['metricas'] = None
                ativo_com_metricas['score_quantitativo'] = 0
        else:  # Ativo genérico
            # Verificar se é ativo direto para usar métricas do ativo de referência
            if ativo.get('tipo') and ativo.get('tipo').startswith('Ativo Direto'):
                classe_ativo = ativo.get('classe_ativo')
                print(f"[DEBUG] Buscando métricas do ativo de referência para {ativo.get('ativo_nome')} (classe: {classe_ativo})")
                metricas_referencia = buscar_metricas_ativo_referencia(cursor, classe_ativo, periodo_dias)
                if metricas_referencia:
                    ativo_com_metricas['metricas'] = metricas_referencia
                    ativo_com_metricas['score_quantitativo'] = calcular_score_quantitativo(metricas_referencia, classe_ativo)
                    
                    # Usar período de dados do ativo de referência se disponível, senão calcular
                    if metricas_referencia.get('periodo_dados'):
                        ativo_com_metricas['periodo_dados'] = metricas_referencia['periodo_dados']
                        print(f"[DEBUG] Métricas do ativo de referência aplicadas - período: {metricas_referencia['periodo_dados']['data_inicial']} a {metricas_referencia['periodo_dados']['data_final']}")
                    else:
                        # Definir período de dados se não existir
                        if not ativo_com_metricas.get('periodo_dados'):
                            data_atual = datetime.now()
                            data_inicial = (data_atual - timedelta(days=periodo_dias)).strftime('%d/%m/%Y')
                            data_final = data_atual.strftime('%d/%m/%Y')
                            ativo_com_metricas['periodo_dados'] = {
                                'data_inicial': data_inicial,
                                'data_final': data_final
                            }
                        print(f"[DEBUG] Métricas do ativo de referência aplicadas")
                else:
                    ativo_com_metricas['metricas'] = None
                    ativo_com_metricas['score_quantitativo'] = 0
                    print(f"[DEBUG] Não foi possível obter métricas do ativo de referência para {classe_ativo}")
            else:
                ativo_com_metricas['metricas'] = None
                ativo_com_metricas['score_quantitativo'] = 0
        carteira_com_metricas.append(ativo_com_metricas)
    return carteira_com_metricas

def calcular_metricas_carteira_consolidada(carteira_com_metricas):
    """
    Calcula métricas consolidadas da carteira completa
    """
    ativos_com_metricas = [a for a in carteira_com_metricas if a.get('metricas')]
    
    if not ativos_com_metricas:
        return None
    
    # Calcular métricas ponderadas por alocação
    retorno_ponderado = 0
    volatilidade_ponderada = 0
    sharpe_ponderado = 0
    drawdown_ponderado = 0
    beta_ponderado = 0
    var_ponderado = 0
    cvar_ponderado = 0
    
    total_alocacao = sum(a['alocacao'] for a in ativos_com_metricas)
    
    for ativo in ativos_com_metricas:
        peso = ativo['alocacao'] / total_alocacao if total_alocacao > 0 else 0
        metricas = ativo['metricas']
        
        retorno_ponderado += metricas['retorno_anualizado'] * peso
        volatilidade_ponderada += metricas['volatilidade_anualizada'] * peso
        sharpe_ponderado += metricas['sharpe_ratio'] * peso
        drawdown_ponderado += metricas['max_drawdown'] * peso
        beta_ponderado += metricas['beta'] * peso
        var_ponderado += metricas['var_95'] * peso
        cvar_ponderado += metricas['cvar_95'] * peso
    
    return {
        'retorno_anualizado': retorno_ponderado,
        'volatilidade_anualizada': volatilidade_ponderada,
        'sharpe_ratio': sharpe_ponderado,
        'max_drawdown': drawdown_ponderado,
        'beta': beta_ponderado,
        'var_95': var_ponderado,
        'cvar_95': cvar_ponderado,
        'total_ativos_com_metricas': len(ativos_com_metricas),
        'total_ativos': len(carteira_com_metricas)
    }

def realizar_backtest_carteira(cursor, carteira_com_metricas, data_inicio, data_fim):
    """
    Realiza backtest da carteira para um período específico
    """
    try:
        # Buscar dados históricos para todos os ativos da carteira
        ativos_especificos = [a for a in carteira_com_metricas if a.get('ativo_id')]
        
        if not ativos_especificos:
            return None
        
        # Buscar dados históricos
        tickers = [ativo['ticker'] for ativo in ativos_especificos]
        placeholders = ','.join(['%s'] * len(tickers))
        
        # Buscar dados históricos - a tabela dados_cmd armazena dados em JSON
        cursor.execute(f"""
            SELECT ativo, dados
            FROM dados_cmd
            WHERE ativo IN ({placeholders})
        """, (*tickers,))
        
        dados_historicos = cursor.fetchall()
        
        if not dados_historicos:
            return None
        
        # Organizar dados por ativo - processar dados JSON
        dados_por_ativo = {}
        todas_datas = set()
        
        for row in dados_historicos:
            ativo = row['ativo']
            dados_json = json.loads(row['dados'])
            
            # Extrair dados da série histórica
            serie_dados = dados_json.get('dados', [])
            dados_por_ativo[ativo] = []
            
            for item in serie_dados:
                if 'data' in item and 'valor' in item and item['valor'] is not None:
                    data_str = item['data']
                    valor = float(item['valor'])
                    
                    # Converter data para datetime para filtro
                    try:
                        data_dt = datetime.strptime(data_str, '%d/%m/%Y')
                        if data_inicio <= data_dt.strftime('%Y-%m-%d') <= data_fim:
                            dados_por_ativo[ativo].append({
                                'data': data_dt,
                                'valor': valor
                            })
                            todas_datas.add(data_dt)
                    except ValueError:
                        # Tentar formato alternativo
                        try:
                            data_dt = datetime.strptime(data_str, '%Y-%m-%d')
                            if data_inicio <= data_str <= data_fim:
                                dados_por_ativo[ativo].append({
                                    'data': data_dt,
                                    'valor': valor
                                })
                                todas_datas.add(data_dt)
                        except ValueError:
                            continue
        
        # Calcular retornos diários da carteira
        datas_unicas = sorted(todas_datas)
        retornos_carteira = []
        valor_carteira = 100  # Valor inicial da carteira
        
        for i, data in enumerate(datas_unicas):
            if i == 0:
                continue
            
            retorno_diario_carteira = 0
            
            for ativo in ativos_especificos:
                ticker = ativo['ticker']
                alocacao = ativo['alocacao'] / 100  # Converter para decimal
                
                if ticker in dados_por_ativo:
                    dados_ativo = dados_por_ativo[ticker]
                    # Encontrar valores para a data atual e anterior
                    valor_atual = None
                    valor_anterior = None
                    
                    # Buscar valor atual
                    for dado in dados_ativo:
                        if dado['data'] == data:
                            valor_atual = dado['valor']
                            break
                    
                    # Buscar valor anterior (data anterior)
                    if i > 0:
                        data_anterior = datas_unicas[i-1]
                        for dado in dados_ativo:
                            if dado['data'] == data_anterior:
                                valor_anterior = dado['valor']
                                break
                    
                    if valor_atual is not None and valor_anterior is not None and valor_anterior != 0:
                        retorno_ativo = (valor_atual - valor_anterior) / valor_anterior
                        retorno_diario_carteira += retorno_ativo * alocacao
            
            # Atualizar valor da carteira
            valor_carteira *= (1 + retorno_diario_carteira)
            
            retornos_carteira.append({
                'data': data.strftime('%Y-%m-%d'),
                'valor': valor_carteira,
                'retorno_diario': retorno_diario_carteira
            })
        
        # Calcular métricas do backtest
        if len(retornos_carteira) > 1:
            retornos_diarios = [r['retorno_diario'] for r in retornos_carteira]
            
            # Retorno total
            retorno_total = (valor_carteira - 100) / 100
            
            # Retorno anualizado
            dias_periodo = len(retornos_carteira)
            retorno_anualizado = (1 + retorno_total) ** (252 / dias_periodo) - 1
            
            # Volatilidade anualizada
            volatilidade_anualizada = np.std(retornos_diarios) * np.sqrt(252)
            
            # Sharpe Ratio (assumindo taxa livre de risco de 12% a.a.)
            taxa_livre_risco = 0.12
            sharpe_ratio = (retorno_anualizado - taxa_livre_risco) / volatilidade_anualizada if volatilidade_anualizada > 0 else 0
            
            # Máximo drawdown
            valores_carteira = [r['valor'] for r in retornos_carteira]
            max_drawdown = calcular_max_drawdown(valores_carteira)
            
            # VaR e CVaR
            var_95 = np.percentile(retornos_diarios, 5)
            cvar_95 = np.mean([r for r in retornos_diarios if r <= var_95])
            
            return {
                'series_historica': retornos_carteira,
                'metricas_backtest': {
                    'retorno_total': retorno_total,
                    'retorno_anualizado': retorno_anualizado,
                    'volatilidade_anualizada': volatilidade_anualizada,
                    'sharpe_ratio': sharpe_ratio,
                    'max_drawdown': max_drawdown,
                    'var_95': var_95,
                    'cvar_95': cvar_95,
                    'periodo_dias': dias_periodo,
                    'valor_final': valor_carteira
                }
            }
        
        return None
        
    except Exception as e:
        print(f"Erro ao realizar backtest: {str(e)}")
        return None

@carteira_cliente_bp.route('/api/carteira-cliente/gerar', methods=['POST'])
@token_required
def gerar_carteira_cliente(current_user=None):
    """
    Gera uma carteira personalizada para o cliente com base no seu perfil de risco,
    score de suitability, notas qualitativas e ativos disponíveis.
    """
    connection = None
    cursor = None
    try:
        data = request.get_json()
        
        if not data or 'cliente_id' not in data or 'mes_referencia' not in data:
            return jsonify({'error': 'Cliente ID e mês de referência são obrigatórios'}), 400
            
        cliente_id = data['cliente_id']
        mes_referencia = data['mes_referencia']
        forcar_regeneracao = data.get('forcar_regeneracao', False)
        max_ativos_por_classe = data.get('max_ativos_por_classe', {})  # Novo parâmetro opcional
        periodo_dias = data.get('periodo_dias', 365)  # Período em dias (padrão: 1 ano)
        filtros_personalizados = data.get('filtros_personalizados', {})  # Filtros personalizados
        
        print(f"DEBUG: Cliente ID: {cliente_id}")
        print(f"DEBUG: Mês Referência: {mes_referencia}")
        print(f"DEBUG: Forçar Regeneração: {forcar_regeneracao}")
        print(f"DEBUG: Tipo do parâmetro forcar_regeneracao: {type(forcar_regeneracao)}")
        print(f"DEBUG: Período em dias: {periodo_dias}")
        print(f"DEBUG: Filtros personalizados: {filtros_personalizados}")
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # 1. Obter dados do cliente
        cursor.execute(
            "SELECT id, client_name, risk_profile, score FROM client WHERE id = %s", 
            (cliente_id,)
        )
        cliente = cursor.fetchone()
        
        if not cliente:
            return jsonify({'error': 'Cliente não encontrado'}), 404
            
        perfil_risco = cliente['risk_profile']
        score_suitability = cliente['score']
        
        # 2. Verificar se já existe uma carteira para este cliente e mês
        cursor.execute("""
            SELECT id, detalhes, data_geracao
            FROM carteira_cliente
            WHERE cliente_id = %s AND mes_referencia = %s
            ORDER BY data_geracao DESC
            LIMIT 1
        """, (cliente_id, mes_referencia))
        
        carteira_existente = cursor.fetchone()
        
        print(f"DEBUG: Carteira existente encontrada: {carteira_existente is not None}")
        
        if carteira_existente and not forcar_regeneracao:
            print("DEBUG: Verificando modificações (regeneração normal)")
            # Verificar se há modificações que justifiquem regenerar
            try:
                detalhes_existentes = json.loads(carteira_existente['detalhes'])
                precisa_regenerar, motivo = verificar_modificacoes_ativos(cursor, detalhes_existentes, mes_referencia)
                
                print(f"DEBUG: Precisa regenerar: {precisa_regenerar}, Motivo: {motivo}")
                
                if not precisa_regenerar:
                    print("DEBUG: Retornando carteira existente")
                    # Adicionar explicações da metodologia à carteira existente se não existir
                    if 'explicacoes_metodologia' not in detalhes_existentes:
                        # Buscar dados necessários para gerar explicações
                        cursor.execute("""
                            SELECT classe_ativo, banda_inferior, banda_neutra, banda_superior
                            FROM carteira_recomendada
                            WHERE perfil = %s AND mes_referencia = %s
                        """, (detalhes_existentes['perfil_risco'], mes_referencia))
                        
                        carteira_recomendada_existente = cursor.fetchall()
                        
                        cursor.execute("""
                            SELECT classe_ativo, nota
                            FROM avaliacao_classe_ativo
                            WHERE mes_referencia = %s
                        """, (mes_referencia,))
                        
                        avaliacoes_existentes = {row['classe_ativo']: row['nota'] for row in cursor.fetchall()}
                        
                        explicacoes_metodologia = gerar_explicacao_metodologia(detalhes_existentes, carteira_recomendada_existente, avaliacoes_existentes)
                        detalhes_existentes['explicacoes_metodologia'] = explicacoes_metodologia
                        
                        # Atualizar a carteira no banco com as explicações
                        cursor.execute("""
                            UPDATE carteira_cliente 
                            SET detalhes = %s
                            WHERE id = %s
                        """, (json.dumps(detalhes_existentes), carteira_existente['id']))
                        connection.commit()
                    
                    # Retornar a carteira existente
                    return jsonify({
                        'carteira_cliente': detalhes_existentes,
                        'mensagem': 'Carteira existente recuperada',
                        'data_geracao': carteira_existente['data_geracao'].strftime('%Y-%m-%d %H:%M:%S'),
                        'regenerada': False
                    }), 200
                else:
                    # Há modificações, continuar com a regeneração
                    print(f"DEBUG: Regenerando carteira devido a: {motivo}")
                    
            except Exception as e:
                print(f"DEBUG: Erro ao verificar carteira existente: {str(e)}")
                # Em caso de erro, continuar com a regeneração
        elif carteira_existente and forcar_regeneracao:
            print("DEBUG: Regeneração forçada solicitada pelo usuário - ignorando verificação de modificações")
            print("DEBUG: Continuando com a geração da nova carteira...")
        else:
            print("DEBUG: Nova carteira ou regeneração forçada - continuando com geração")
        
        print("DEBUG: Iniciando processo de geração de nova carteira...")
        
        # 3. Calcular perfil ponderado
        perfil_ponderado = calcular_perfil_ponderado(score_suitability, perfil_risco)
        
        # 4. Obter carteira recomendada para o perfil e mês
        cursor.execute("""
            SELECT classe_ativo, banda_inferior, banda_neutra, banda_superior
            FROM carteira_recomendada
            WHERE perfil = %s AND mes_referencia = %s
        """, (perfil_risco, mes_referencia))
        
        carteira_recomendada = cursor.fetchall()
        
        if not carteira_recomendada:
            return jsonify({'error': f'Não existe carteira recomendada para o perfil {perfil_risco} no mês {mes_referencia}'}), 404
        
        # 5. Obter notas qualitativas (asset class evaluation) para o mês
        cursor.execute("""
            SELECT classe_ativo, nota
            FROM avaliacao_classe_ativo
            WHERE mes_referencia = %s
        """, (mes_referencia,))
        
        avaliacoes = {row['classe_ativo']: row['nota'] for row in cursor.fetchall()}
        
        # 6. Gerar carteira otimizada
        carteira_otimizada = []
        total_alocado = 0
        
        apenas_passam_filtros = data.get('apenas_passam_filtros', False)
        periodo_carteira = {'data_inicial': None, 'data_final': None}
        for classe in carteira_recomendada:
            classe_ativo = classe['classe_ativo']
            
            # Obter nota qualitativa (padrão 0 se não existir)
            nota_qualitativa = avaliacoes.get(classe_ativo, 0)
            
            # Otimizar alocação
            alocacao = otimizar_alocacao({
                'banda_inferior': classe['banda_inferior'],
                'banda_neutra': classe['banda_neutra'],
                'banda_superior': classe['banda_superior']
            }, nota_qualitativa, perfil_ponderado)
            
            # Buscar ativos disponíveis para esta classe
            ativos_disponiveis = obter_ativos_por_classe_quantitativo(cursor, classe_ativo, mes_referencia, periodo_dias)
            
            # Definir max_ativos para esta classe
            max_ativos = max_ativos_por_classe.get(classe_ativo, 4)
            
            # Distribuir ativos baseado no perfil do cliente e max_ativos
            distribuicao_ativos = distribuir_ativos_por_perfil(
                ativos_disponiveis, alocacao, perfil_risco, classe_ativo, max_ativos=max_ativos, apenas_passam_filtros=apenas_passam_filtros, filtros_personalizados=filtros_personalizados
            )
            
            for item in distribuicao_ativos:
                ativo_selecionado = item['ativo']
                alocacao_para_ativo = item['alocacao']
                passou_filtros = item.get('passou_filtros', False)
                if ativo_selecionado.get('id'): # Ativo específico
                    # Buscar série histórica para pegar período
                    serie_historica = buscar_serie_historica_ativo(cursor, ativo_selecionado, periodo_dias)
                    data_inicial = serie_historica[0]['data'] if serie_historica else None
                    data_final = serie_historica[-1]['data'] if serie_historica else None
                    # Atualizar período consolidado
                    if data_inicial and (periodo_carteira['data_inicial'] is None or data_inicial < periodo_carteira['data_inicial']):
                        periodo_carteira['data_inicial'] = data_inicial
                    if data_final and (periodo_carteira['data_final'] is None or data_final > periodo_carteira['data_final']):
                        periodo_carteira['data_final'] = data_final
                    ativo_carteira = {
                        'classe_ativo': classe_ativo,
                        'ativo_id': ativo_selecionado['id'],
                        'ativo_nome': ativo_selecionado['nome'],
                        'ticker': ativo_selecionado['ticker'],
                        'isin': ativo_selecionado['isin'],
                        'cnpj': ativo_selecionado['cnpj'],
                        'gestora': ativo_selecionado['gestora'],
                        'alocacao': round(alocacao_para_ativo, 2),
                        'tipo': 'Ativo Específico',
                        'passou_filtros': passou_filtros,
                        'periodo_dados': {'data_inicial': data_inicial, 'data_final': data_final}
                    }
                    if 'metricas' in ativo_selecionado:
                        ativo_carteira['metricas'] = ativo_selecionado['metricas']
                        ativo_carteira['score_quantitativo'] = ativo_selecionado.get('score_quantitativo', 0)
                    carteira_otimizada.append(ativo_carteira)
                else:
                    ativo_generico = obter_ativo_generico_por_classe(classe_ativo)
                    ativo_carteira = {
                        'classe_ativo': classe_ativo,
                        'ativo_id': None,
                        'ativo_nome': ativo_generico['nome'],
                        'ticker': ativo_generico['ticker'],
                        'isin': None,
                        'cnpj': None,
                        'gestora': None,
                        'alocacao': round(alocacao_para_ativo, 2),
                        'tipo': ativo_generico['tipo'],
                        'passou_filtros': passou_filtros,
                        'periodo_dados': None
                    }
                    
                    # Se for ativo direto, buscar métricas do ativo de referência
                    if ativo_generico['tipo'].startswith('Ativo Direto'):
                        print(f"[DEBUG] Aplicando métricas do ativo de referência ao {ativo_generico['nome']}")
                        metricas_referencia = buscar_metricas_ativo_referencia(cursor, classe_ativo, periodo_dias)
                        if metricas_referencia:
                            ativo_carteira['metricas'] = metricas_referencia
                            ativo_carteira['score_quantitativo'] = calcular_score_quantitativo(metricas_referencia, classe_ativo)
                            
                            # Usar período de dados do ativo de referência se disponível, senão calcular
                            if metricas_referencia.get('periodo_dados'):
                                ativo_carteira['periodo_dados'] = metricas_referencia['periodo_dados']
                                print(f"[DEBUG] Métricas do ativo de referência aplicadas - período: {metricas_referencia['periodo_dados']['data_inicial']} a {metricas_referencia['periodo_dados']['data_final']}")
                            else:
                                # Definir período de dados se não existir
                                data_atual = datetime.now()
                                data_inicial = (data_atual - timedelta(days=periodo_dias)).strftime('%d/%m/%Y')
                                data_final = data_atual.strftime('%d/%m/%Y')
                                ativo_carteira['periodo_dados'] = {
                                    'data_inicial': data_inicial,
                                    'data_final': data_final
                                }
                                print(f"[DEBUG] Métricas do ativo de referência aplicadas")
                        else:
                            print(f"[DEBUG] Não foi possível obter métricas do ativo de referência para {classe_ativo}")
                    
                    carteira_otimizada.append(ativo_carteira)
            
            total_alocado += alocacao
        
        # 7. Normalizar alocações para totalizar 100%
        if total_alocado > 0:
            fator_normalizacao = 100 / total_alocado
            for item in carteira_otimizada:
                item['alocacao'] = round(item['alocacao'] * fator_normalizacao, 2)
        
        # 8. Gerar explicações da metodologia
        carteira_cliente_temp = {
            'cliente_id': cliente_id,
            'cliente_nome': cliente['client_name'],
            'perfil_risco': perfil_risco,
            'score_suitability': score_suitability,
            'perfil_ponderado': perfil_ponderado,
            'mes_referencia': mes_referencia,
            'notas_qualitativas': avaliacoes,
            'carteira_original': carteira_recomendada,
            'carteira_otimizada': carteira_otimizada,
            'total_alocado': round(total_alocado, 2),
            'observacoes': f'Carteira gerada com base no perfil {perfil_risco} (score: {score_suitability}), notas qualitativas do mês {mes_referencia} e ativos disponíveis.',
            'periodo_dados_carteira': periodo_carteira
        }
        explicacoes_metodologia = gerar_explicacao_metodologia(carteira_cliente_temp, carteira_recomendada, avaliacoes)
        
        # 9. Preparar resposta
        carteira_cliente = {
            'cliente_id': cliente_id,
            'cliente_nome': cliente['client_name'],
            'perfil_risco': perfil_risco,
            'score_suitability': score_suitability,
            'perfil_ponderado': perfil_ponderado,
            'mes_referencia': mes_referencia,
            'notas_qualitativas': avaliacoes,
            'carteira_original': carteira_recomendada,
            'carteira_otimizada': carteira_otimizada,
            'total_alocado': round(total_alocado, 2),
            'observacoes': f'Carteira gerada com base no perfil {perfil_risco} (score: {score_suitability}), notas qualitativas do mês {mes_referencia} e ativos disponíveis.',
            'explicacoes_metodologia': explicacoes_metodologia,
            'periodo_dados_carteira': periodo_carteira
        }
        
        # 9. Registrar a carteira gerada no banco de dados
        data_geracao = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Verificar se a tabela carteira_cliente existe
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS carteira_cliente (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    cliente_id INT NOT NULL,
                    user_id INT NOT NULL,
                    mes_referencia VARCHAR(7) NOT NULL,
                    data_geracao DATETIME NOT NULL,
                    detalhes JSON NOT NULL,
                    FOREIGN KEY (cliente_id) REFERENCES client(id),
                    FOREIGN KEY (user_id) REFERENCES user(id)
                )
            """)
            connection.commit()
        except Error as e:
            print(f"Erro ao verificar/criar tabela carteira_cliente: {str(e)}")
        
        # Se já existe uma carteira, atualizar; senão, inserir nova
        if carteira_existente:
            cursor.execute("""
                UPDATE carteira_cliente 
                SET data_geracao = %s, detalhes = %s
                WHERE id = %s
            """, (
                data_geracao,
                json.dumps(carteira_cliente),
                carteira_existente['id']
            ))
            carteira_id = carteira_existente['id']
            acao = 'Atualização de carteira personalizada otimizada'
        else:
            cursor.execute("""
                INSERT INTO carteira_cliente 
                (cliente_id, user_id, mes_referencia, data_geracao, detalhes)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                cliente_id,
                request.user_id,
                mes_referencia,
                data_geracao,
                json.dumps(carteira_cliente)
            ))
            carteira_id = cursor.lastrowid
            acao = 'Geração de carteira personalizada otimizada'
        
        # Registrar no histórico do cliente
        cursor.execute("""
            INSERT INTO client_history 
            (user_id, client_id, action_type, action_date, details)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            request.user_id,
            cliente_id,
            'UPDATE',
            data_geracao,
            json.dumps({
                'acao': acao,
                'mes_referencia': mes_referencia,
                'perfil_ponderado': perfil_ponderado,
                'carteira_id': carteira_id
            })
        ))
        
        connection.commit()
        
        print(f"DEBUG: Carteira gerada com sucesso. Regenerada: True")
        print(f"DEBUG: Mensagem: Carteira gerada com sucesso")
        print(f"DEBUG: Data geração: {data_geracao}")
        
        return jsonify({
            'carteira_cliente': carteira_cliente,
            'mensagem': 'Carteira gerada com sucesso',
            'data_geracao': data_geracao,
            'regenerada': True
        }), 200

    except Error as e:
        if connection:
            connection.rollback()
        return jsonify({'error': f'Erro ao gerar carteira: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()

@carteira_cliente_bp.route('/api/carteira-cliente/historico/<int:cliente_id>', methods=['GET'])
@token_required
def get_historico_carteiras(cliente_id, current_user=None):
    """
    Retorna o histórico de carteiras geradas para um cliente específico.
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Verificar se o cliente existe
        cursor.execute("SELECT id FROM client WHERE id = %s", (cliente_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Cliente não encontrado'}), 404
        
        # Verificar se o usuário tem acesso a este cliente
        if request.user_role != 'Admin':
            cursor.execute("""
                SELECT id FROM client 
                WHERE id = %s AND user_id = %s
            """, (cliente_id, request.user_id))
            if not cursor.fetchone():
                return jsonify({'error': 'Acesso negado a este cliente'}), 403
        
        # Obter histórico de carteiras
        cursor.execute("""
            SELECT 
                id, mes_referencia, data_geracao, detalhes
            FROM 
                carteira_cliente
            WHERE 
                cliente_id = %s
            ORDER BY 
                data_geracao DESC
        """, (cliente_id,))
        
        historico = cursor.fetchall()
        
        # Formatar datas para string
        for item in historico:
            if 'data_geracao' in item and item['data_geracao']:
                item['data_geracao'] = item['data_geracao'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Converter JSON para dicionário
            if 'detalhes' in item and item['detalhes']:
                try:
                    item['detalhes'] = json.loads(item['detalhes'])
                except:
                    pass
        
        return jsonify({'historico': historico}), 200

    except Error as e:
        return jsonify({'error': f'Erro ao buscar histórico de carteiras: {str(e)}'}), 500
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close() 

def obter_ativos_por_classe_quantitativo(cursor, classe_ativo, mes_referencia, periodo_dias=365):
    """
    Busca ativos aprovados por classe de investimento com métricas quantitativas
    """
    return calcular_metricas_quantitativas(cursor, classe_ativo, mes_referencia, limite_ativos=10, periodo_dias=periodo_dias)

def distribuir_ativos_por_perfil(ativos_disponiveis, alocacao_classe, perfil_risco, classe_ativo, max_ativos=4, apenas_passam_filtros=False, filtros_personalizados=None):
    """
    Distribui ativos baseado no perfil do cliente e no máximo de ativos definido
    Só inclui ativos que possuem métricas calculáveis
    Se apenas_passam_filtros=True, só inclui ativos que passam nos filtros
    Caso contrário, preenche até o máximo permitido, sinalizando se passou ou não
    """
    if not ativos_disponiveis:
        return [{'ativo': {}, 'alocacao': alocacao_classe, 'passou_filtros': False}]
    
    # Estratégias por perfil - usar filtros personalizados se fornecidos, senão usar padrão
    if filtros_personalizados and perfil_risco in filtros_personalizados:
        filtros = filtros_personalizados[perfil_risco]
        estrategias = {
            'Conservador': {
                'concentracao_principal': 0.7,
                'filtros': filtros
            },
            'Moderado': {
                'concentracao_principal': 0.5,
                'filtros': filtros
            },
            'Sofisticado': {
                'concentracao_principal': 0.4,
                'filtros': filtros
            }
        }
    else:
        # Filtros padrão
        estrategias = {
            'Conservador': {
                'concentracao_principal': 0.7,
                'filtros': {'max_volatilidade': 0.20, 'max_drawdown': 0.15, 'min_sharpe': 0.2}
            },
            'Moderado': {
                'concentracao_principal': 0.5,
                'filtros': {'max_volatilidade': 0.30, 'max_drawdown': 0.20, 'min_sharpe': 0.1}
            },
            'Sofisticado': {
                'concentracao_principal': 0.4,
                'filtros': {'max_volatilidade': 0.40, 'max_drawdown': 0.30, 'min_sharpe': 0.0}
            }
        }
    
    estrategia = estrategias.get(perfil_risco, estrategias['Moderado'])
    
    # Filtrar apenas ativos com métricas válidas
    ativos_com_metricas = []
    for ativo in ativos_disponiveis:
        if ('metricas' in ativo and 
            ativo['metricas'] and 
            ativo['metricas'].get('retorno_anualizado') is not None and
            ativo['metricas'].get('volatilidade_anualizada') is not None and
            ativo['metricas'].get('sharpe_ratio') is not None and
            ativo['metricas'].get('max_drawdown') is not None):
            ativos_com_metricas.append(ativo)
    
    print(f"[DEBUG] Classe {classe_ativo}: {len(ativos_disponiveis)} ativos disponíveis, {len(ativos_com_metricas)} com métricas válidas")
    
    # Se não há ativos com métricas válidas, verificar se há ativo de referência disponível
    if not ativos_com_metricas:
        print(f"[DEBUG] Classe {classe_ativo}: Nenhum ativo com métricas válidas encontrado")
        # Verificar se existe ativo de referência para esta classe
        mapeamento_ativos = {
            'Pós-Fixado': 'CDI',
            'Inflação': 'ANBIMA_IMAB',
            'Pré-Fixado': 'ANBIMA_IRFM',
            'Multimercado': 'ANBIMA_IHFA',
            'Renda Variável Brasil': 'IBOV',
            'Fundos Listados': 'IFIX',
            'Renda Fixa Global': 'Bloomberg_US_Aggregate',
            'Renda Variável Internacional': 'US:SP500'
        }
        
        ativo_referencia = mapeamento_ativos.get(classe_ativo)
        if ativo_referencia:
            print(f"[DEBUG] Classe {classe_ativo}: Tentando usar métricas do {ativo_referencia} para ativo direto")
            # Criar ativo genérico com métricas do ativo de referência
            ativo_generico = obter_ativo_generico_por_classe(classe_ativo)
            return [{'ativo': ativo_generico, 'alocacao': alocacao_classe, 'passou_filtros': True}]
        else:
            return [{'ativo': {}, 'alocacao': alocacao_classe, 'passou_filtros': False}]
    
    # Filtrar ativos com métricas baseado no perfil
    ativos_filtrados = []
    ativos_nao_filtrados = []
    for ativo in ativos_com_metricas:
        metricas = ativo['metricas']
        filtros = estrategia['filtros']
        passou = (
            metricas.get('volatilidade_anualizada', 1) <= filtros['max_volatilidade'] and
            metricas.get('max_drawdown', 1) <= filtros['max_drawdown'] and
            metricas.get('sharpe_ratio', 0) >= filtros['min_sharpe']
        )
        if passou:
            ativos_filtrados.append({**ativo, 'score_perfil': calcular_score_perfil_ativo(ativo, perfil_risco, classe_ativo), 'passou_filtros': True})
        else:
            ativos_nao_filtrados.append({**ativo, 'score_perfil': calcular_score_perfil_ativo(ativo, perfil_risco, classe_ativo), 'passou_filtros': False})
    
    # Ordenar por score
    ativos_filtrados.sort(key=lambda x: x.get('score_perfil', 0), reverse=True)
    ativos_nao_filtrados.sort(key=lambda x: x.get('score_perfil', 0), reverse=True)
    
    print(f"[DEBUG] Classe {classe_ativo}: {len(ativos_filtrados)} ativos passaram nos filtros, {len(ativos_nao_filtrados)} não passaram")
    
    # Se apenas_passam_filtros, só retorna os que passaram
    if apenas_passam_filtros:
        ativos_selecionados = ativos_filtrados[:max_ativos]
        print(f"[DEBUG] Classe {classe_ativo}: Modo 'apenas_passam_filtros' - selecionados {len(ativos_selecionados)} ativos")
    else:
        # Preencher até o máximo permitido
        ativos_selecionados = ativos_filtrados[:max_ativos]
        if len(ativos_selecionados) < max_ativos:
            faltam = max_ativos - len(ativos_selecionados)
            ativos_selecionados += ativos_nao_filtrados[:faltam]
        print(f"[DEBUG] Classe {classe_ativo}: Modo normal - selecionados {len(ativos_selecionados)} ativos")
    
    # Distribuir alocação
    distribuicao = []
    if len(ativos_selecionados) == 0:
        # Se não há ativos selecionados, retornar ativo genérico
        print(f"[DEBUG] Classe {classe_ativo}: Nenhum ativo selecionado, usando ativo genérico")
        distribuicao.append({'ativo': {}, 'alocacao': alocacao_classe, 'passou_filtros': False})
    elif len(ativos_selecionados) == 1:
        distribuicao.append({'ativo': ativos_selecionados[0], 'alocacao': alocacao_classe, 'passou_filtros': ativos_selecionados[0].get('passou_filtros', False)})
    else:
        alocacao_principal = alocacao_classe * estrategia['concentracao_principal']
        alocacao_restante = alocacao_classe - alocacao_principal
        distribuicao.append({'ativo': ativos_selecionados[0], 'alocacao': alocacao_principal, 'passou_filtros': ativos_selecionados[0].get('passou_filtros', False)})
        if len(ativos_selecionados) > 1:
            alocacao_por_ativo = alocacao_restante / (len(ativos_selecionados) - 1)
            for ativo in ativos_selecionados[1:]:
                distribuicao.append({'ativo': ativo, 'alocacao': alocacao_por_ativo, 'passou_filtros': ativo.get('passou_filtros', False)})
    
    print(f"[DEBUG] Classe {classe_ativo}: Distribuição final com {len(distribuicao)} ativos")
    return distribuicao

def calcular_metricas_ativo(serie_historica):
    """
    Calcula métricas quantitativas para um ativo
    Retorna None se não conseguir calcular as métricas adequadamente
    """
    if len(serie_historica) < 30:
        print(f"[DEBUG] Série histórica insuficiente: {len(serie_historica)} pontos (mínimo 30)")
        return None
    
    try:
        # Extrair valores e calcular retornos diários
        valores = [float(item['valor']) for item in serie_historica]
        
        # Verificar se há valores válidos
        if len(valores) < 2:
            print(f"[DEBUG] Poucos valores válidos: {len(valores)}")
            return None
        
        # Verificar se há valores zero ou negativos
        if any(v <= 0 for v in valores):
            print(f"[DEBUG] Valores zero ou negativos encontrados")
            return None
        
        retornos_diarios = []
        retornos_raw = []
        for i in range(1, len(valores)):
            if valores[i-1] != 0 and not np.isnan(valores[i-1]) and not np.isnan(valores[i]):
                retorno = (valores[i] - valores[i-1]) / valores[i-1]
                retornos_raw.append(retorno)
                # Filtrar retornos absurdos (>30% ou <-30%)
                if -0.3 < retorno < 0.3:
                    retornos_diarios.append(retorno)
        
        # Log dos maiores retornos para auditoria
        if len(retornos_raw) > 0:
            top_abs = sorted(retornos_raw, key=lambda x: abs(x), reverse=True)[:5]
            print(f"[DEBUG] Top 5 retornos diários absolutos: {[f'{r:.2%}' for r in top_abs]}")
        
        if len(retornos_diarios) < 20:
            print(f"[DEBUG] Retornos válidos insuficientes: {len(retornos_diarios)} (mínimo 20)")
            return None
        
        # Calcular métricas básicas
        retorno_medio_diario = np.mean(retornos_diarios)
        retorno_total = (valores[-1] - valores[0]) / valores[0]
        dias_periodo = len(retornos_diarios)
        
        # Verificar se o retorno total é válido
        if np.isnan(retorno_total) or np.isinf(retorno_total):
            print(f"[DEBUG] Retorno total inválido: {retorno_total}")
            return None
        
        retorno_anualizado = (1 + retorno_total) ** (252 / dias_periodo) - 1
        volatilidade_anualizada = np.std(retornos_diarios) * np.sqrt(252)
        
        # Verificar se as métricas são válidas
        if (np.isnan(retorno_anualizado) or np.isinf(retorno_anualizado) or
            np.isnan(volatilidade_anualizada) or np.isinf(volatilidade_anualizada)):
            print(f"[DEBUG] Métricas inválidas - retorno: {retorno_anualizado}, volatilidade: {volatilidade_anualizada}")
            return None
        
        # Sharpe Ratio (assumindo taxa livre de risco de 12% a.a.)
        taxa_livre_risco = 0.12
        sharpe_ratio = (retorno_anualizado - taxa_livre_risco) / volatilidade_anualizada if volatilidade_anualizada > 0 else 0
        
        # Máximo drawdown
        max_drawdown = calcular_max_drawdown(valores)
        
        # Beta simplificado
        beta = calcular_beta_simplificado(retornos_diarios)
        
        # VaR e CVaR 95%
        var_95 = np.percentile(retornos_diarios, 5)
        cvar_95 = np.mean([r for r in retornos_diarios if r <= var_95])
        
        # Verificar se todas as métricas são válidas
        metricas = {
            'retorno_medio_diario': retorno_medio_diario,
            'retorno_anualizado': retorno_anualizado,
            'volatilidade_anualizada': volatilidade_anualizada,
            'sharpe_ratio': sharpe_ratio,
            'retorno_total': retorno_total,
            'max_drawdown': max_drawdown,
            'beta': beta,
            'var_95': var_95,
            'cvar_95': cvar_95,
            'periodo_dias': dias_periodo
        }
        
        # Verificar se alguma métrica é NaN ou Inf
        for key, value in metricas.items():
            if key != 'periodo_dias' and (np.isnan(value) or np.isinf(value)):
                print(f"[DEBUG] Métrica {key} inválida: {value}")
                return None
        
        print(f"[DEBUG] Métricas calculadas com sucesso - retorno: {retorno_anualizado:.2%}, volatilidade: {volatilidade_anualizada:.2%}, sharpe: {sharpe_ratio:.3f}")
        return metricas
        
    except Exception as e:
        print(f"Erro ao calcular métricas: {str(e)}")
        return None

def calcular_max_drawdown(valores):
    """
    Calcula o máximo drawdown de uma série de valores
    """
    if len(valores) < 2:
        return 0
    
    max_dd = 0
    pico = valores[0]
    
    for valor in valores:
        if valor > pico:
            pico = valor
        else:
            drawdown = (pico - valor) / pico
            max_dd = max(max_dd, drawdown)
    
    return max_dd

def calcular_beta_simplificado(retornos_diarios):
    """
    Calcula beta simplificado (assumindo mercado com retorno médio de 0.0005 por dia)
    """
    if len(retornos_diarios) < 10:
        return 1.0
    
    # Retorno médio do mercado (aproximação)
    retorno_mercado_medio = 0.0005
    
    # Calcular covariância e variância
    retornos_mercado = [retorno_mercado_medio] * len(retornos_diarios)
    
    covariancia = np.cov(retornos_diarios, retornos_mercado)[0, 1]
    variancia_mercado = np.var(retornos_mercado)
    
    if variancia_mercado == 0:
        return 1.0
    
    beta = covariancia / variancia_mercado
    return max(0, min(3, beta))  # Limitar entre 0 e 3

def obter_pesos_por_classe(classe_ativo):
    """
    Retorna os pesos para cálculo do score quantitativo por classe de ativo
    """
    pesos = {
        'Renda Variável Brasil': {
            'sharpe': 0.25,
            'retorno': 0.35,
            'volatilidade': 0.15,
            'drawdown': 0.15,
            'beta': 0.10
        },
        'Renda Variável Internacional': {
            'sharpe': 0.25,
            'retorno': 0.35,
            'volatilidade': 0.15,
            'drawdown': 0.15,
            'beta': 0.10
        },
        'Pós-Fixado': {
            'sharpe': 0.35,
            'retorno': 0.20,
            'volatilidade': 0.25,
            'drawdown': 0.15,
            'beta': 0.05
        },
        'Pré-Fixado': {
            'sharpe': 0.35,
            'retorno': 0.20,
            'volatilidade': 0.25,
            'drawdown': 0.15,
            'beta': 0.05
        },
        'Inflação': {
            'sharpe': 0.35,
            'retorno': 0.20,
            'volatilidade': 0.25,
            'drawdown': 0.15,
            'beta': 0.05
        },
        'Multimercado': {
            'sharpe': 0.30,
            'retorno': 0.25,
            'volatilidade': 0.20,
            'drawdown': 0.15,
            'beta': 0.10
        },
        'Fundos Listados': {
            'sharpe': 0.25,
            'retorno': 0.30,
            'volatilidade': 0.20,
            'drawdown': 0.15,
            'beta': 0.10
        },
        'Alternativos': {
            'sharpe': 0.20,
            'retorno': 0.25,
            'volatilidade': 0.20,
            'drawdown': 0.20,
            'beta': 0.15
        },
        'Renda Fixa Global': {
            'sharpe': 0.35,
            'retorno': 0.20,
            'volatilidade': 0.25,
            'drawdown': 0.15,
            'beta': 0.05
        }
    }
    
    return pesos.get(classe_ativo, {
        'sharpe': 0.25,
        'retorno': 0.25,
        'volatilidade': 0.20,
        'drawdown': 0.20,
        'beta': 0.10
    })

def calcular_score_quantitativo(metricas, classe_ativo):
    """
    Calcula score quantitativo baseado nas métricas e classe do ativo
    """
    if not metricas:
        return 0
    
    pesos = obter_pesos_por_classe(classe_ativo)
    
    # Normalizar métricas (0-1)
    sharpe_norm = min(1.0, max(0.0, metricas.get('sharpe_ratio', 0) / 2.0))
    retorno_norm = min(1.0, max(0.0, metricas.get('retorno_anualizado', 0) / 0.30))
    volatilidade_norm = min(1.0, max(0.0, 1 - metricas.get('volatilidade_anualizada', 1) / 0.50))
    drawdown_norm = min(1.0, max(0.0, 1 - metricas.get('max_drawdown', 1) / 0.30))
    beta_norm = min(1.0, max(0.0, 1 - abs(metricas.get('beta', 1) - 1) / 2.0))
    
    # Calcular score ponderado
    score = (
        pesos['sharpe'] * sharpe_norm +
        pesos['retorno'] * retorno_norm +
        pesos['volatilidade'] * volatilidade_norm +
        pesos['drawdown'] * drawdown_norm +
        pesos['beta'] * beta_norm
    )
    
    return score

def calcular_score_perfil_ativo(ativo, perfil_risco, classe_ativo):
    """
    Calcula score específico para o perfil do cliente
    """
    if 'metricas' not in ativo:
        return 0
    
    metricas = ativo['metricas']
    score_base = calcular_score_quantitativo(metricas, classe_ativo)
    
    # Ajustes baseados no perfil
    ajustes_perfil = {
        'Conservador': {
            'volatilidade_penalidade': 0.3,
            'drawdown_penalidade': 0.3,
            'sharpe_bonus': 0.2
        },
        'Moderado': {
            'volatilidade_penalidade': 0.1,
            'drawdown_penalidade': 0.1,
            'sharpe_bonus': 0.1
        },
        'Sofisticado': {
            'volatilidade_penalidade': 0.0,
            'drawdown_penalidade': 0.0,
            'sharpe_bonus': 0.0
        }
    }
    
    ajuste = ajustes_perfil.get(perfil_risco, ajustes_perfil['Moderado'])
    
    # Aplicar ajustes
    volatilidade_norm = min(1.0, max(0.0, metricas.get('volatilidade_anualizada', 1) / 0.50))
    drawdown_norm = min(1.0, max(0.0, metricas.get('max_drawdown', 1) / 0.30))
    sharpe_norm = min(1.0, max(0.0, metricas.get('sharpe_ratio', 0) / 2.0))
    
    score_ajustado = score_base
    score_ajustado -= ajuste['volatilidade_penalidade'] * (1 - volatilidade_norm)
    score_ajustado -= ajuste['drawdown_penalidade'] * (1 - drawdown_norm)
    score_ajustado += ajuste['sharpe_bonus'] * sharpe_norm
    
    return max(0, min(1, score_ajustado))

def gerar_explicacao_estrategia_perfil(perfil_risco):
    """
    Gera explicação da estratégia de distribuição baseada no perfil
    """
    estrategias = {
        'Conservador': """
            • Máximo de 2 ativos por classe
            • Concentração: 70% no principal ativo
            • Foco: Preservação de capital e estabilidade
            • Filtros rigorosos: Baixa volatilidade (<15%), baixo drawdown (<10%), alto Sharpe (>0.5)
            • Prioriza ativos com características defensivas
        """,
        'Moderado': """
            • Máximo de 3 ativos por classe
            • Concentração: 50% no principal ativo
            • Foco: Crescimento com controle de risco
            • Filtros moderados: Volatilidade média (<25%), drawdown moderado (<15%), Sharpe adequado (>0.3)
            • Equilibra crescimento e proteção
        """,
        'Sofisticado': """
            • Máximo de 4 ativos por classe
            • Concentração: 40% no principal ativo
            • Foco: Crescimento agressivo com gestão de risco
            • Filtros flexíveis: Aceita maior volatilidade (<35%), drawdown maior (<25%), Sharpe mínimo (>0.2)
            • Prioriza potencial de retorno com gestão de risco
        """
    }
    
    return estrategias.get(perfil_risco, estrategias['Moderado'])

@carteira_cliente_bp.route('/api/carteira-cliente/relatorio-backtest', methods=['POST'])
@token_required
def gerar_relatorio_backtest(current_user=None):
    """
    Gera relatório completo de backtest da carteira
    """
    try:
        data = request.get_json()
        
        if not data or 'carteira_id' not in data:
            return jsonify({'error': 'ID da carteira é obrigatório'}), 400
        
        carteira_id = data['carteira_id']
        data_inicio = data.get('data_inicio', '2023-01-01')
        data_fim = data.get('data_fim', '2024-12-31')
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Buscar carteira
        cursor.execute("""
            SELECT detalhes FROM carteira_cliente WHERE id = %s
        """, (carteira_id,))
        
        carteira_row = cursor.fetchone()
        if not carteira_row:
            return jsonify({'error': 'Carteira não encontrada'}), 404
        
        carteira_cliente = json.loads(carteira_row['detalhes'])
        
        # Calcular métricas completas para cada ativo
        carteira_com_metricas = calcular_metricas_carteira_completa(
            cursor, 
            carteira_cliente['carteira_otimizada'], 
            carteira_cliente['mes_referencia'],
            periodo_dias=365
        )
        
        # Calcular métricas consolidadas da carteira
        metricas_consolidadas = calcular_metricas_carteira_consolidada(carteira_com_metricas)
        
        # Realizar backtest
        resultado_backtest = realizar_backtest_carteira(
            cursor, 
            carteira_com_metricas, 
            data_inicio, 
            data_fim
        )
        
        # Gerar relatório
        relatorio = {
            'informacoes_carteira': {
                'cliente_nome': carteira_cliente['cliente_nome'],
                'perfil_risco': carteira_cliente['perfil_risco'],
                'score_suitability': carteira_cliente['score_suitability'],
                'mes_referencia': carteira_cliente['mes_referencia'],
                'data_geracao': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            'metricas_consolidadas': metricas_consolidadas,
            'ativos_detalhados': carteira_com_metricas,
            'backtest': resultado_backtest,
            'periodo_backtest': {
                'data_inicio': data_inicio,
                'data_fim': data_fim
            }
        }
        
        return jsonify(relatorio), 200
        
    except Exception as e:
        return jsonify({'error': f'Erro ao gerar relatório: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close() 

def buscar_metricas_cdi(cursor, periodo_dias=365):
    """
    Busca e calcula métricas do CDI para ativos diretos Pós-Fixado
    """
    try:
        # Buscar dados do CDI na tabela dados_cmd
        cursor.execute("SELECT dados FROM dados_cmd WHERE ativo = 'CDI'")
        row = cursor.fetchone()
        
        if not row:
            print("[DEBUG] CDI não encontrado na tabela dados_cmd")
            return None
        
        dados_json = json.loads(row['dados'])
        serie_dados = dados_json.get('dados', [])
        
        if not serie_dados:
            print("[DEBUG] Série de dados do CDI vazia")
            return None
        
        # Converter para formato esperado e filtrar por período
        serie_historica = []
        data_atual = datetime.now()
        data_limite = data_atual - timedelta(days=periodo_dias)
        
        for item in serie_dados:
            if 'data' in item and 'valor' in item and item['valor'] is not None:
                try:
                    # Tentar diferentes formatos de data
                    if '/' in item['data']:
                        data = datetime.strptime(item['data'], '%d/%m/%Y')
                    else:
                        data = datetime.strptime(item['data'], '%Y-%m-%d')
                    
                    # Filtrar apenas dados do período especificado
                    if data >= data_limite:
                        # Para CDI, o valor já é a taxa anual, então convertemos para retorno diário
                        taxa_anual = float(item['valor']) / 100  # Converter de % para decimal
                        retorno_diario = (1 + taxa_anual) ** (1/252) - 1  # Converter para retorno diário
                        
                        serie_historica.append({
                            'data': item['data'],
                            'valor': retorno_diario  # Armazenar como retorno diário
                        })
                except ValueError:
                    continue
        
        if len(serie_historica) < 30:
            print(f"[DEBUG] Série histórica do CDI insuficiente: {len(serie_historica)} pontos")
            return None
        
        # Calcular métricas do CDI
        metricas = calcular_metricas_cdi_especificas(serie_historica)
        
        if metricas:
            # Adicionar informações sobre o período dos dados
            if serie_historica:
                data_inicial = serie_historica[0]['data']
                data_final = serie_historica[-1]['data']
                metricas['periodo_dados'] = {
                    'data_inicial': data_inicial,
                    'data_final': data_final
                }
            print(f"[DEBUG] Métricas do CDI calculadas com sucesso - retorno: {metricas['retorno_anualizado']:.2%}")
            return metricas
        else:
            print("[DEBUG] Erro ao calcular métricas do CDI")
            return None
            
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar métricas do CDI: {str(e)}")
        return None

def calcular_metricas_cdi_especificas(serie_historica):
    """
    Calcula métricas específicas para o CDI baseado nos retornos diários
    """
    try:
        # Extrair retornos diários
        retornos_diarios = [float(item['valor']) for item in serie_historica]
        
        if len(retornos_diarios) < 20:
            return None
        
        # Calcular métricas básicas
        retorno_medio_diario = np.mean(retornos_diarios)
        dias_periodo = len(retornos_diarios)
        
        # Calcular retorno total acumulado
        retorno_total = 1
        for retorno in retornos_diarios:
            retorno_total *= (1 + retorno)
        retorno_total -= 1
        
        # Retorno anualizado
        retorno_anualizado = (1 + retorno_total) ** (252 / dias_periodo) - 1
        
        # Volatilidade anualizada (CDI tem baixa volatilidade)
        volatilidade_anualizada = np.std(retornos_diarios) * np.sqrt(252)
        
        # Sharpe Ratio (assumindo taxa livre de risco de 12% a.a.)
        taxa_livre_risco = 0.12
        sharpe_ratio = (retorno_anualizado - taxa_livre_risco) / volatilidade_anualizada if volatilidade_anualizada > 0 else 0
        
        # Máximo drawdown (CDI tem drawdown muito baixo)
        max_drawdown = calcular_max_drawdown_cdi(retornos_diarios)
        
        # Beta (CDI tem beta próximo a zero)
        beta = 0.01  # Beta muito baixo para CDI
        
        # VaR e CVaR 95% (CDI tem risco muito baixo)
        var_95 = np.percentile(retornos_diarios, 5)
        cvar_95 = np.mean([r for r in retornos_diarios if r <= var_95])
        
        metricas = {
            'retorno_medio_diario': retorno_medio_diario,
            'retorno_anualizado': retorno_anualizado,
            'volatilidade_anualizada': volatilidade_anualizada,
            'sharpe_ratio': sharpe_ratio,
            'retorno_total': retorno_total,
            'max_drawdown': max_drawdown,
            'beta': beta,
            'var_95': var_95,
            'cvar_95': cvar_95,
            'periodo_dias': dias_periodo
        }
        
        # Verificar se todas as métricas são válidas
        for key, value in metricas.items():
            if key != 'periodo_dias' and (np.isnan(value) or np.isinf(value)):
                print(f"[DEBUG] Métrica CDI {key} inválida: {value}")
                return None
        
        return metricas
        
    except Exception as e:
        print(f"[DEBUG] Erro ao calcular métricas específicas do CDI: {str(e)}")
        return None

def calcular_max_drawdown_cdi(retornos_diarios):
    """
    Calcula máximo drawdown específico para CDI (baseado em retornos diários)
    """
    if len(retornos_diarios) < 2:
        return 0
    
    # Calcular valores acumulados
    valores_acumulados = [1]
    for retorno in retornos_diarios:
        valores_acumulados.append(valores_acumulados[-1] * (1 + retorno))
    
    # Calcular drawdown
    max_dd = 0
    pico = valores_acumulados[0]
    
    for valor in valores_acumulados:
        if valor > pico:
            pico = valor
        else:
            drawdown = (pico - valor) / pico
            max_dd = max(max_dd, drawdown)
    
    return max_dd

def buscar_metricas_ativo_referencia(cursor, classe_ativo, periodo_dias=365):
    """
    Busca e calcula métricas do ativo de referência para cada classe de ativo direto
    """
    # Mapeamento de classes para ativos de referência
    mapeamento_ativos = {
        'Pós-Fixado': 'CDI',
        'Inflação': 'ANBIMA_IMAB',
        'Pré-Fixado': 'ANBIMA_IRFM',
        'Multimercado': 'ANBIMA_IHFA',
        'Renda Variável Brasil': 'IBOV',
        'Fundos Listados': 'IFIX',
        'Renda Fixa Global': 'Bloomberg_US_Aggregate',
        'Renda Variável Internacional': 'US:SP500'
    }
    
    ativo_referencia = mapeamento_ativos.get(classe_ativo)
    if not ativo_referencia:
        print(f"[DEBUG] Nenhum ativo de referência definido para classe: {classe_ativo}")
        return None
    
    print(f"[DEBUG] Buscando métricas do ativo de referência: {ativo_referencia} para classe: {classe_ativo}")
    
    try:
        # Buscar dados do ativo de referência na tabela dados_cmd
        cursor.execute("SELECT dados FROM dados_cmd WHERE ativo = %s", (ativo_referencia,))
        row = cursor.fetchone()
        
        if not row:
            print(f"[DEBUG] {ativo_referencia} não encontrado na tabela dados_cmd")
            return None
        
        dados_json = json.loads(row['dados'])
        serie_dados = dados_json.get('dados', [])
        
        if not serie_dados:
            print(f"[DEBUG] Série de dados do {ativo_referencia} vazia")
            return None
        
        # Converter para formato esperado e filtrar por período
        serie_historica = []
        data_atual = datetime.now()
        data_limite = data_atual - timedelta(days=periodo_dias)
        
        for item in serie_dados:
            if 'data' in item and 'valor' in item and item['valor'] is not None:
                try:
                    # Tentar diferentes formatos de data
                    if '/' in item['data']:
                        data = datetime.strptime(item['data'], '%d/%m/%Y')
                    else:
                        data = datetime.strptime(item['data'], '%Y-%m-%d')
                    
                    # Filtrar apenas dados do período especificado
                    if data >= data_limite:
                        valor = float(item['valor'])
                        
                        # Para CDI, converter taxa anual para retorno diário
                        if ativo_referencia == 'CDI':
                            taxa_anual = valor / 100  # Converter de % para decimal
                            retorno_diario = (1 + taxa_anual) ** (1/252) - 1
                            serie_historica.append({
                                'data': item['data'],
                                'valor': retorno_diario
                            })
                        else:
                            # Para outros ativos, usar valor direto (cotação)
                            serie_historica.append({
                                'data': item['data'],
                                'valor': valor
                            })
                except ValueError:
                    continue
        
        if len(serie_historica) < 30:
            print(f"[DEBUG] Série histórica do {ativo_referencia} insuficiente: {len(serie_historica)} pontos")
            return None
        
        # Calcular métricas específicas para o tipo de ativo
        if ativo_referencia == 'CDI':
            metricas = calcular_metricas_cdi_especificas(serie_historica)
        else:
            metricas = calcular_metricas_ativo(serie_historica)
        
        if metricas:
            # Adicionar informações sobre o período dos dados
            if serie_historica:
                data_inicial = serie_historica[0]['data']
                data_final = serie_historica[-1]['data']
                metricas['periodo_dados'] = {
                    'data_inicial': data_inicial,
                    'data_final': data_final
                }
            print(f"[DEBUG] Métricas do {ativo_referencia} calculadas com sucesso - retorno: {metricas['retorno_anualizado']:.2%}")
            return metricas
        else:
            print(f"[DEBUG] Erro ao calcular métricas do {ativo_referencia}")
            return None
            
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar métricas do {ativo_referencia}: {str(e)}")
        return None

def obter_ativo_referencia_por_classe(classe_ativo):
    """
    Retorna informações do ativo de referência para cada classe
    """
    ativos_referencia = {
        'Pós-Fixado': {
            'nome': 'CDI (Ativo Direto)',
            'ticker': 'CDI',
            'tipo': 'Ativo Direto Pós-Fixado',
            'descricao': 'Certificado de Depósito Interbancário'
        },
        'Inflação': {
            'nome': 'ANBIMA IMAB (Ativo Direto)',
            'ticker': 'ANBIMA_IMAB',
            'tipo': 'Ativo Direto Inflação',
            'descricao': 'Índice de Mercado Anbima - Inflação'
        },
        'Pré-Fixado': {
            'nome': 'ANBIMA IRFM (Ativo Direto)',
            'ticker': 'ANBIMA_IRFM',
            'tipo': 'Ativo Direto Pré-Fixado',
            'descricao': 'Índice de Renda Fixa Mercado Anbima'
        },
        'Multimercado': {
            'nome': 'ANBIMA IHFA (Ativo Direto)',
            'ticker': 'ANBIMA_IHFA',
            'tipo': 'Ativo Direto Multimercado',
            'descricao': 'Índice Hedge Fund Anbima'
        },
        'Renda Variável Brasil': {
            'nome': 'IBOV (Ativo Direto)',
            'ticker': 'IBOV',
            'tipo': 'Ativo Direto Renda Variável',
            'descricao': 'Índice Bovespa'
        },
        'Fundos Listados': {
            'nome': 'IFIX (Ativo Direto)',
            'ticker': 'IFIX',
            'tipo': 'Ativo Direto ETF',
            'descricao': 'Índice de Fundos Imobiliários'
        },
        'Renda Fixa Global': {
            'nome': 'Bloomberg US Aggregate (Ativo Direto)',
            'ticker': 'Bloomberg_US_Aggregate',
            'tipo': 'Ativo Direto Renda Fixa Global',
            'descricao': 'Bloomberg US Aggregate Bond Index'
        },
        'Renda Variável Internacional': {
            'nome': 'S&P 500 (Ativo Direto)',
            'ticker': 'US:SP500',
            'tipo': 'Ativo Direto Renda Variável Internacional',
            'descricao': 'Standard & Poor\'s 500'
        }
    }
    
    return ativos_referencia.get(classe_ativo, {
        'nome': f'{classe_ativo} (Ativo Direto)',
        'ticker': classe_ativo,
        'tipo': f'Ativo Direto {classe_ativo}',
        'descricao': f'Ativo direto {classe_ativo}'
    })

def gerar_relatorio_pdf_carteira(carteira_cliente, carteira_com_metricas=None):
    """
    Gera um relatório PDF completo da carteira com gráficos e tabelas detalhadas
    """
    try:
        # Criar buffer para o PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        
        # Estilos minimalistas com dourado e branco
        styles = getSampleStyleSheet()
        
        # Definir cores dourado
        dourado_escuro = colors.Color(0.8, 0.6, 0.2)  # Dourado escuro
        dourado_claro = colors.Color(0.95, 0.85, 0.6)  # Dourado claro
        dourado_medio = colors.Color(0.9, 0.75, 0.4)   # Dourado médio
        
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=20,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=dourado_escuro,
            fontName='Helvetica-Bold',
            textDecoration='underline'
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=12,
            spaceBefore=20,
            textColor=dourado_escuro,
            fontName='Helvetica-Bold',
            textDecoration='underline'
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.black,
            fontName='Helvetica'
        )
        
        # 1. Cabeçalho do Relatório
        story.append(Paragraph("RELATÓRIO DE CARTEIRA DE INVESTIMENTO", title_style))
        story.append(Spacer(1, 20))
        
        # Informações do Cliente
        cliente_info = [
            ['Cliente:', carteira_cliente['cliente_nome']],
            ['Perfil de Risco:', carteira_cliente['perfil_risco']],
            ['Score Suitability:', str(carteira_cliente['score_suitability'])],
            ['Mês de Referência:', carteira_cliente['mes_referencia']],
            ['Data de Geração:', datetime.now().strftime('%d/%m/%Y %H:%M:%S')]
        ]
        
        cliente_table = Table(cliente_info, colWidths=[2*inch, 4*inch])
        cliente_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), dourado_claro),
            ('BACKGROUND', (1, 0), (1, -1), colors.white),
            ('TEXTCOLOR', (0, 0), (0, -1), dourado_escuro),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
        ]))
        story.append(cliente_table)
        story.append(Spacer(1, 20))
        
        # 2. Perfil Ponderado
        story.append(Paragraph("ANÁLISE DE PERFIL PONDERADO", heading_style))
        perfil_ponderado = carteira_cliente['perfil_ponderado']
        perfil_info = [
            ['Perfil Principal:', f"{perfil_ponderado['perfil_principal']} ({perfil_ponderado['peso_principal']*100:.0f}%)"],
            ['Perfil Secundário:', f"{perfil_ponderado['perfil_secundario']} ({perfil_ponderado['peso_secundario']*100:.0f}%)"]
        ]
        
        perfil_table = Table(perfil_info, colWidths=[2*inch, 4*inch])
        perfil_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), dourado_claro),
            ('BACKGROUND', (1, 0), (1, -1), colors.white),
            ('TEXTCOLOR', (0, 0), (0, -1), dourado_escuro),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
        ]))
        story.append(perfil_table)
        story.append(Spacer(1, 20))
        
        # 3. Notas Qualitativas
        story.append(Paragraph("NOTAS QUALITATIVAS POR CLASSE DE ATIVO", heading_style))
        notas_data = [['Classe de Ativo', 'Nota Qualitativa']]
        for classe, nota in carteira_cliente['notas_qualitativas'].items():
            notas_data.append([classe, f"{nota:+d}"])
        
        notas_table = Table(notas_data, colWidths=[3*inch, 3*inch])
        notas_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dourado_escuro),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
        ]))
        story.append(notas_table)
        story.append(PageBreak())
        
        # 4. Alocação por Classe
        story.append(Paragraph("ALOCAÇÃO POR CLASSE DE ATIVO", heading_style))
        
        # Agrupar ativos por classe
        alocacao_por_classe = {}
        for ativo in carteira_cliente['carteira_otimizada']:
            classe = ativo['classe_ativo']
            if classe not in alocacao_por_classe:
                alocacao_por_classe[classe] = 0
            alocacao_por_classe[classe] += ativo['alocacao']
        
        alocacao_data = [['Classe de Ativo', 'Alocação (%)']]
        for classe, alocacao in sorted(alocacao_por_classe.items()):
            alocacao_data.append([classe, f"{alocacao:.2f}%"])
        
        alocacao_table = Table(alocacao_data, colWidths=[4*inch, 2*inch])
        alocacao_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dourado_escuro),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
        ]))
        story.append(alocacao_table)
        story.append(Spacer(1, 20))
        
        # 5. Gráfico de Alocação
        if alocacao_por_classe:
            fig, ax = plt.subplots(figsize=(8, 6))
            classes = list(alocacao_por_classe.keys())
            valores = list(alocacao_por_classe.values())
            
            # Cores dourado para o gráfico
            cores_dourado = ['#D4AF37', '#FFD700', '#F4E4BC', '#E6C200', '#B8860B', '#DAA520']
            colors_pie = cores_dourado[:len(classes)]
            
            wedges, texts, autotexts = ax.pie(valores, labels=classes, autopct='%1.1f%%', 
                                             colors=colors_pie, startangle=90)
            ax.set_title('Distribuição da Carteira por Classe de Ativo', 
                        fontsize=14, fontweight='bold', color='#8B7355', pad=20)
            
            # Estilizar textos
            for text in texts:
                text.set_fontsize(10)
                text.set_color('#8B7355')
            
            for autotext in autotexts:
                autotext.set_fontsize(9)
                autotext.set_color('white')
                autotext.set_fontweight('bold')
            
            # Salvar gráfico temporariamente
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', dpi=300, bbox_inches='tight', 
                       facecolor='white', edgecolor='none')
            img_buffer.seek(0)
            plt.close()
            
            # Adicionar gráfico ao PDF
            img = Image(img_buffer)
            img.drawHeight = 4*inch
            img.drawWidth = 6*inch
            story.append(img)
            story.append(PageBreak())
        
        # 6. Detalhamento dos Ativos por Classe
        story.append(Paragraph("DETALHAMENTO DOS ATIVOS POR CLASSE", heading_style))
        
        # Agrupar ativos por classe
        ativos_por_classe = {}
        for ativo in carteira_cliente['carteira_otimizada']:
            classe = ativo['classe_ativo']
            if classe not in ativos_por_classe:
                ativos_por_classe[classe] = []
            ativos_por_classe[classe].append(ativo)
        
        # Tabela resumo de alocação por ativo
        story.append(Paragraph("RESUMO DE ALOCAÇÃO POR ATIVO", ParagraphStyle(
            'SubHeading',
            parent=styles['Heading3'],
            fontSize=12,
            spaceAfter=15,
            spaceBefore=20,
            textColor=dourado_escuro,
            fontName='Helvetica-Bold',
            textDecoration='underline'
        )))
        
        # Preparar dados da tabela resumo - um ativo por linha
        resumo_alocacao_data = [['Classe de Ativo', 'Nome do Ativo', 'Alocação (%)', 'Tipo']]
        
        # Função para truncar texto
        def truncar_texto(texto, max_caracteres=35):
            if len(texto) <= max_caracteres:
                return texto
            return texto[:max_caracteres-3] + "..."
        
        # Função para truncar classe de ativo (coluna menor)
        def truncar_classe(texto, max_caracteres=18):
            if len(texto) <= max_caracteres:
                return texto
            return texto[:max_caracteres-3] + "..."
        
        # Função para truncar tipo (coluna menor)
        def truncar_tipo(texto, max_caracteres=12):
            if len(texto) <= max_caracteres:
                return texto
            return texto[:max_caracteres-3] + "..."
        
        # Função para determinar o nome a ser exibido na tabela resumo
        def obter_nome_exibicao(ativo):
            # Se tem ticker (fundos listados), usar o ticker
            if ativo.get('ticker') and ativo['ticker'].strip():
                return ativo['ticker']
            # Caso contrário, usar o nome do ativo
            return ativo['ativo_nome']
        
        for classe, ativos in ativos_por_classe.items():
            for ativo in ativos:
                nome_exibicao = obter_nome_exibicao(ativo)
                resumo_alocacao_data.append([
                    truncar_classe(classe.upper()),
                    truncar_texto(nome_exibicao),
                    f"{ativo['alocacao']:.2f}%",
                    truncar_tipo(ativo['tipo'])
                ])
        
        # Criar tabela resumo
        resumo_alocacao_table = Table(resumo_alocacao_data, colWidths=[2*inch, 3*inch, 1.5*inch, 1.5*inch])
        resumo_alocacao_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), dourado_escuro),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),  # Nome da classe
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),  # Nome do ativo
            ('ALIGN', (2, 1), (2, -1), 'CENTER'),  # Alocação
            ('ALIGN', (3, 1), (3, -1), 'CENTER'),  # Tipo
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('WORDWRAP', (0, 0), (-1, -1), True),
        ]))
        
        story.append(resumo_alocacao_table)
        story.append(Spacer(1, 25))
        
        # Criar seção para cada classe de ativo
        for classe, ativos in ativos_por_classe.items():
            # Calcular alocação total da classe
            alocacao_classe = sum(ativo['alocacao'] for ativo in ativos)
            
            # Título da classe
            story.append(Paragraph(f"CLASSE: {classe.upper()} - Alocação Total: {alocacao_classe:.2f}%", ParagraphStyle(
                'ClasseTitle',
                parent=styles['Heading3'],
                fontSize=13,
                spaceAfter=12,
                spaceBefore=20,
                textColor=dourado_escuro,
                fontName='Helvetica-Bold',
                borderWidth=0,
                borderColor=dourado_escuro,
                borderPadding=2,
                leftIndent=0,
                rightIndent=0,
                textDecoration='underline'
            )))
            
            # Tabela resumo da classe
            resumo_classe = [
                ['Total de Ativos:', str(len(ativos))],
                ['Alocação Total:', f"{alocacao_classe:.2f}%"],
                ['Ativos Diretos:', str(sum(1 for a in ativos if a['tipo'] == 'Direto'))],
                ['Ativos Específicos:', str(sum(1 for a in ativos if a['tipo'] == 'Específico'))]
            ]
            
            resumo_table = Table(resumo_classe, colWidths=[2*inch, 4*inch])
            resumo_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), dourado_claro),
                ('BACKGROUND', (1, 0), (1, -1), colors.white),
                ('TEXTCOLOR', (0, 0), (0, -1), dourado_escuro),
                ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
            ]))
            story.append(resumo_table)
            story.append(Spacer(1, 15))
            
            # Detalhamento de cada ativo da classe
            for i, ativo in enumerate(ativos, 1):
                metricas = ativo.get('metricas', {})
                
                # Título do ativo
                story.append(Paragraph(f"Ativo {i}: {ativo['ativo_nome']}", ParagraphStyle(
                    'AtivoTitle',
                    parent=styles['Heading4'],
                    fontSize=11,
                    spaceAfter=8,
                    textColor=dourado_escuro,
                    fontName='Helvetica-Bold',
                    textDecoration='underline'
                )))
                
                # Informações básicas do ativo
                ativo_info = [
                    ['Tipo:', ativo['tipo']],
                    ['Ticker:', ativo.get('ticker', 'N/A')],
                    ['ISIN:', ativo.get('isin', 'N/A')],
                    ['CNPJ:', ativo.get('cnpj', 'N/A')],
                    ['Gestora:', ativo.get('gestora', 'N/A')],
                    ['Alocação:', f"{ativo['alocacao']:.2f}%"]
                ]
                
                # Adicionar métricas se disponíveis
                if metricas:
                    ativo_info.extend([
                        ['Retorno Anualizado:', f"{metricas.get('retorno_anualizado', 0)*100:.2f}%"],
                        ['Volatilidade Anualizada:', f"{metricas.get('volatilidade_anualizada', 0)*100:.2f}%"],
                        ['Sharpe Ratio:', f"{metricas.get('sharpe_ratio', 0):.3f}"],
                        ['Máximo Drawdown:', f"{metricas.get('max_drawdown', 0)*100:.2f}%"],
                        ['Beta:', f"{metricas.get('beta', 0):.3f}"],
                        ['VaR 95%:', f"{metricas.get('var_95', 0)*100:.2f}%"],
                        ['CVaR 95%:', f"{metricas.get('cvar_95', 0)*100:.2f}%"],
                        ['Score Quantitativo:', f"{ativo.get('score_quantitativo', 0):.3f}"]
                    ])
                    
                    # Adicionar período dos dados se disponível
                    if ativo.get('periodo_dados'):
                        periodo = ativo['periodo_dados']
                        ativo_info.append(['Período dos Dados:', f"{periodo['data_inicial']} a {periodo['data_final']}"])
                
                # Criar tabela do ativo
                ativo_table = Table(ativo_info, colWidths=[2.5*inch, 3.5*inch])
                ativo_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (0, -1), dourado_claro),
                    ('BACKGROUND', (1, 0), (1, -1), colors.white),
                    ('TEXTCOLOR', (0, 0), (0, -1), dourado_escuro),
                    ('TEXTCOLOR', (1, 0), (1, -1), colors.black),
                    ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                    ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                    ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('WORDWRAP', (0, 0), (-1, -1), True),
                ]))
                
                story.append(ativo_table)
                story.append(Spacer(1, 15))
            
            # Adicionar quebra de página entre classes (exceto na última)
            if list(ativos_por_classe.keys()).index(classe) < len(ativos_por_classe) - 1:
                story.append(PageBreak())
        
        story.append(PageBreak())
        
        # 7. Métricas Consolidadas da Carteira
        if carteira_com_metricas:
            story.append(Paragraph("MÉTRICAS CONSOLIDADAS DA CARTEIRA", heading_style))
            
            # Calcular métricas consolidadas
            metricas_consolidadas = calcular_metricas_carteira_consolidada(carteira_com_metricas)
            
            if metricas_consolidadas:
                metricas_data = [
                    ['Métrica', 'Valor'],
                    ['Retorno Anualizado', f"{metricas_consolidadas['retorno_anualizado']*100:.2f}%"],
                    ['Volatilidade Anualizada', f"{metricas_consolidadas['volatilidade_anualizada']*100:.2f}%"],
                    ['Sharpe Ratio', f"{metricas_consolidadas['sharpe_ratio']:.3f}"],
                    ['Máximo Drawdown', f"{metricas_consolidadas['max_drawdown']*100:.2f}%"],
                    ['Beta', f"{metricas_consolidadas['beta']:.3f}"],
                    ['VaR 95%', f"{metricas_consolidadas['var_95']*100:.2f}%"],
                    ['CVaR 95%', f"{metricas_consolidadas['cvar_95']*100:.2f}%"],
                    ['Total de Ativos', f"{metricas_consolidadas['total_ativos']}"],
                    ['Ativos com Métricas', f"{metricas_consolidadas['total_ativos_com_metricas']}"]
                ]
                
                metricas_table = Table(metricas_data, colWidths=[3*inch, 3*inch])
                metricas_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), dourado_escuro),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
                ]))
                story.append(metricas_table)
                story.append(Spacer(1, 20))
        
        # 8. Gráficos de Rentabilidade
        story.append(Paragraph("ANÁLISE DE RENTABILIDADE", heading_style))
        
        # Gerar dados simulados de rentabilidade
        if carteira_com_metricas and metricas_consolidadas:
            # Simular série de retornos baseada nas métricas consolidadas
            retorno_medio_diario = metricas_consolidadas['retorno_anualizado'] / 252
            volatilidade_diaria = metricas_consolidadas['volatilidade_anualizada'] / np.sqrt(252)
            
            # Gerar 252 dias (1 ano)
            np.random.seed(42)  # Para reprodutibilidade
            retornos_diarios = np.random.normal(retorno_medio_diario, volatilidade_diaria, 252)
            
            # Calcular retorno acumulado (corrigido para começar em 0%)
            retorno_acumulado = []
            valor_acumulado = 1.0  # Começar com 100%
            for i, retorno in enumerate(retornos_diarios):
                if i == 0:
                    # Primeiro dia: retorno acumulado é 0%
                    retorno_acumulado.append(0.0)
                else:
                    # Dias seguintes: aplicar o retorno e calcular acumulado
                    valor_acumulado *= (1 + retorno)
                    retorno_acumulado.append((valor_acumulado - 1) * 100)  # Em percentual
            
            # Calcular evolução do patrimônio com R$ 100 inicial
            patrimonio_evolucao = []
            patrimonio_atual = 100.0  # R$ 100 inicial
            for retorno in retornos_diarios:
                patrimonio_atual *= (1 + retorno)
                patrimonio_evolucao.append(patrimonio_atual)
            
            # Garantir consistência: o retorno acumulado final deve corresponder ao ganho percentual do patrimônio
            patrimonio_final = patrimonio_evolucao[-1]
            retorno_final_calculado = ((patrimonio_final - 100) / 100) * 100  # Ganho percentual
            
            # Atualizar o último valor do retorno acumulado para garantir consistência
            if len(retorno_acumulado) > 0:
                retorno_acumulado[-1] = retorno_final_calculado
                retorno_final = retorno_final_calculado
            
            # Criar datas para o eixo X (corrigido para evolução correta)
            data_inicio = datetime.now() - timedelta(days=252)
            datas = [data_inicio + timedelta(days=i) for i in range(252)]
            
            # Gráfico 1: Retorno Acumulado
            fig1, ax1 = plt.subplots(figsize=(10, 6))
            ax1.plot(datas, retorno_acumulado, linewidth=2.5, color='#D4AF37', label='Retorno Acumulado')
            ax1.axhline(y=0, color='#8B7355', linestyle='--', alpha=0.7, label='Linha Base (0%)')
            
            # Adicionar área sombreada
            ax1.fill_between(datas, retorno_acumulado, 0, alpha=0.2, color='#D4AF37')
            
            ax1.set_title('Retorno Acumulado da Carteira', fontsize=14, fontweight='bold', pad=20, color='#8B7355')
            ax1.set_ylabel('Retorno Acumulado (%)', fontsize=12, color='#8B7355')
            ax1.set_xlabel('Data', fontsize=12, color='#8B7355')
            ax1.grid(True, alpha=0.2, color='#D4AF37')
            ax1.legend(frameon=True, facecolor='white', edgecolor='#D4AF37')
            
            # Formatar eixo X
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%Y'))
            ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, color='#8B7355')
            plt.setp(ax1.yaxis.get_majorticklabels(), color='#8B7355')
            
            # Adicionar anotação do retorno final
            ax1.annotate(f'Retorno Final: {retorno_final:.2f}%', 
                        xy=(datas[-1], retorno_final), 
                        xytext=(datas[-30], retorno_final + 5),
                        arrowprops=dict(arrowstyle='->', color='#D4AF37', alpha=0.8),
                        fontsize=10, fontweight='bold', color='#8B7355',
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9, edgecolor='#D4AF37'))
            
            plt.tight_layout()
            
            # Salvar gráfico 1
            img_buffer1 = io.BytesIO()
            plt.savefig(img_buffer1, format='png', dpi=300, bbox_inches='tight', 
                       facecolor='white', edgecolor='none')
            img_buffer1.seek(0)
            plt.close()
            
            # Adicionar gráfico 1 ao PDF
            img1 = Image(img_buffer1)
            img1.drawHeight = 5*inch
            img1.drawWidth = 7*inch
            story.append(img1)
            story.append(Spacer(1, 20))
            
            # Gráfico 2: Evolução do Patrimônio
            fig2, ax2 = plt.subplots(figsize=(10, 6))
            ax2.plot(datas, patrimonio_evolucao, linewidth=2.5, color='#B8860B', label='Patrimônio')
            ax2.axhline(y=100, color='#8B7355', linestyle='--', alpha=0.7, label='Investimento Inicial (R$ 100)')
            
            # Adicionar área sombreada
            ax2.fill_between(datas, patrimonio_evolucao, 100, alpha=0.2, color='#B8860B')
            
            ax2.set_title('Evolução do Patrimônio - Investimento Inicial de R$ 100', fontsize=14, fontweight='bold', pad=20, color='#8B7355')
            ax2.set_ylabel('Patrimônio (R$)', fontsize=12, color='#8B7355')
            ax2.set_xlabel('Data', fontsize=12, color='#8B7355')
            ax2.grid(True, alpha=0.2, color='#B8860B')
            ax2.legend(frameon=True, facecolor='white', edgecolor='#B8860B')
            
            # Formatar eixo X
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%Y'))
            ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, color='#8B7355')
            plt.setp(ax2.yaxis.get_majorticklabels(), color='#8B7355')
            
            # Adicionar anotações
            patrimonio_final = patrimonio_evolucao[-1]
            ganho_total = patrimonio_final - 100
            ax2.annotate(f'Patrimônio Final: R$ {patrimonio_final:.2f}\nGanho: R$ {ganho_total:.2f}', 
                        xy=(datas[-1], patrimonio_final), 
                        xytext=(datas[-30], patrimonio_final + 10),
                        arrowprops=dict(arrowstyle='->', color='#B8860B', alpha=0.8),
                        fontsize=10, fontweight='bold', color='#8B7355',
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.9, edgecolor='#B8860B'))
            
            plt.tight_layout()
            
            # Salvar gráfico 2
            img_buffer2 = io.BytesIO()
            plt.savefig(img_buffer2, format='png', dpi=300, bbox_inches='tight', 
                       facecolor='white', edgecolor='none')
            img_buffer2.seek(0)
            plt.close()
            
            # Adicionar gráfico 2 ao PDF
            img2 = Image(img_buffer2)
            img2.drawHeight = 5*inch
            img2.drawWidth = 7*inch
            story.append(img2)
            story.append(Spacer(1, 20))
            
            # Adicionar resumo estatístico
            story.append(Paragraph("RESUMO DA RENTABILIDADE", heading_style))
            
            resumo_data = [
                ['Métrica', 'Valor'],
                ['Retorno Acumulado Final', f"{retorno_final:.2f}%"],
                ['Patrimônio Final', f"R$ {patrimonio_final:.2f}"],
                ['Ganho Absoluto', f"R$ {ganho_total:.2f}"],
                ['Retorno Médio Diário', f"{retorno_medio_diario*100:.4f}%"],
                ['Volatilidade Diária', f"{volatilidade_diaria*100:.4f}%"],
                ['Melhor Dia', f"{max(retornos_diarios)*100:.2f}%"],
                ['Pior Dia', f"{min(retornos_diarios)*100:.2f}%"],
                ['Dias Positivos', f"{sum(1 for r in retornos_diarios if r > 0)} de 252"],
                ['Dias Negativos', f"{sum(1 for r in retornos_diarios if r < 0)} de 252"]
            ]
            
            resumo_table = Table(resumo_data, colWidths=[3*inch, 3*inch])
            resumo_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), dourado_escuro),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, dourado_medio)
            ]))
            story.append(resumo_table)
            story.append(PageBreak())
        
        # 9. Observações e Metodologia
        story.append(Paragraph("OBSERVAÇÕES E METODOLOGIA", heading_style))
        
        observacoes = [
            "• Esta carteira foi gerada automaticamente pelo sistema IntelliAlloc",
            "• As métricas são calculadas com base em dados históricos dos últimos 365 dias",
            "• O perfil ponderado combina o perfil de risco declarado com o score de suitability",
            "• As notas qualitativas refletem a avaliação mensal das classes de ativo",
            "• Ativos diretos utilizam índices de referência para cálculo de métricas",
            "• A simulação de rentabilidade é baseada nas métricas históricas calculadas",
            "• Recomenda-se revisão periódica da carteira conforme mudanças no perfil do cliente"
        ]
        
        for obs in observacoes:
            story.append(Paragraph(obs, normal_style))
            story.append(Spacer(1, 6))
        
        # Gerar PDF
        doc.build(story)
        buffer.seek(0)
        
        return buffer
        
    except Exception as e:
        print(f"Erro ao gerar relatório PDF: {str(e)}")
        return None

@carteira_cliente_bp.route('/api/carteira-cliente/exportar-pdf', methods=['POST'])
@token_required
def exportar_carteira_pdf(current_user=None):
    """
    Exporta a carteira do cliente em formato PDF com gráficos e métricas detalhadas
    """
    try:
        data = request.get_json()
        
        if not data or 'carteira_id' not in data:
            return jsonify({'error': 'ID da carteira é obrigatório'}), 400
        
        carteira_id = data['carteira_id']
        
        connection = get_db_connection()
        if connection is None:
            return jsonify({'error': 'Erro de conexão com o banco'}), 500

        cursor = connection.cursor(dictionary=True)
        
        # Buscar carteira
        cursor.execute("""
            SELECT detalhes FROM carteira_cliente WHERE id = %s
        """, (carteira_id,))
        
        carteira_row = cursor.fetchone()
        if not carteira_row:
            return jsonify({'error': 'Carteira não encontrada'}), 404
        
        carteira_cliente = json.loads(carteira_row['detalhes'])
        
        # Calcular métricas completas para cada ativo
        carteira_com_metricas = calcular_metricas_carteira_completa(
            cursor, 
            carteira_cliente['carteira_otimizada'], 
            carteira_cliente['mes_referencia'],
            periodo_dias=365
        )
        
        # Gerar PDF
        pdf_buffer = gerar_relatorio_pdf_carteira(carteira_cliente, carteira_com_metricas)
        
        if pdf_buffer is None:
            return jsonify({'error': 'Erro ao gerar relatório PDF'}), 500
        
        # Converter para base64
        pdf_base64 = base64.b64encode(pdf_buffer.getvalue()).decode('utf-8')
        
        # Nome do arquivo
        nome_arquivo = f"carteira_{carteira_cliente['cliente_nome'].replace(' ', '_')}_{carteira_cliente['mes_referencia']}.pdf"
        
        return jsonify({
            'pdf_base64': pdf_base64,
            'nome_arquivo': nome_arquivo,
            'mensagem': 'Relatório PDF gerado com sucesso'
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'Erro ao exportar carteira: {str(e)}'}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()