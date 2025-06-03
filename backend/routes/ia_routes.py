from flask import Blueprint, jsonify, request
from utils.db import get_db_connection
from mysql.connector import Error
from middleware.auth import token_required
import google.generativeai as genai
import os
import re
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

# Configurar a API do Gemini
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash-preview-04-17')

ia_bp = Blueprint('ia', __name__)

def gerar_prompt(carteiras, avaliacoes, perfil, carteira_anterior_texto):
    return f"""
    Você é um especialista em alocação de ativos. Analise as seguintes informações para o perfil {perfil}:

    Carteira Atual:
    {carteira_anterior_texto}
    {carteiras}

    Avaliações das Classes de Ativos:
    {avaliacoes}

    Metodologia de Priorização:
    1. Análise de Mudanças:
       - Compare com a carteira do mês anterior
       - Identifique tendências de mercado
       - Avalie mudanças no cenário macroeconômico

    2. Sistema de Pontuação:
       - Nota 5: Aumento significativo de alocação
       - Nota 4: Aumento moderado de alocação
       - Nota 3: Manutenção da alocação
       - Nota 2: Redução moderada de alocação
       - Nota 1: Redução significativa de alocação

    3. Fatores de Decisão:
       - Avaliação institucional (peso 40%)
       - Mudança de cenário macroeconômico (peso 30%)
       - Performance histórica (peso 20%)
       - Liquidez e volatilidade (peso 10%)

    Considere o cenário macroeconômico atual:
    - Taxa de juros global e local
    - Inflação e expectativas de inflação
    - Crescimento econômico
    - Tensões geopolíticas
    - Mercado de trabalho
    - Ciclo econômico atual
    - Políticas monetárias e fiscais
    - Tendências tecnológicas e disruptivas

    Considere que:
    1. A soma das bandas neutras deve ser 100%
    2. A banda neutra deve estar entre a banda inferior e superior
    3. O perfil {perfil} tem características específicas que devem ser consideradas
    4. Algumas classes de ativos são mais adequadas para certos perfis
    5. As avaliações institucionais devem influenciar a alocação (notas mais altas indicam maior atratividade)

    Retorne sua resposta no seguinte formato:
    [VALORES]
    [valores numéricos das bandas neutras, separados por vírgula]

    [EXPLICAÇÃO]
    [explicação detalhada das escolhas para cada perfil(Conservador, Moderado e Sofisticado), incluindo:
    - Análise comparativa com o mês anterior
    - Pontuação atribuída a cada classe de ativo
    - Justificativa baseada na metodologia de priorização
    - Impacto do cenário macroeconômico
    - Considerações específicas para cada perfil(Conservador, Moderado e Sofisticado)]
    """

@ia_bp.route('/api/ia/sugerir-banda-neutra', methods=['POST'])
@token_required
def sugerir_banda_neutra(current_user=None):
    try:
        data = request.get_json()
        carteiras = data.get('carteiras', [])
        mes_atual = data.get('mes_atual', '')
        perfil = data.get('perfil', '')
        avaliacoes = data.get('avaliacoes', [])

        if not carteiras or not mes_atual or not perfil:
            return jsonify({'error': 'Dados incompletos fornecidos'}), 400

        # Buscar carteira do mês anterior
        try:
            connection = get_db_connection()
            if connection is None:
                return jsonify({'error': 'Erro de conexão com o banco'}), 500

            cursor = connection.cursor(dictionary=True)
            
            # Converter mes_atual para datetime e subtrair um mês
            mes_anterior = (datetime.strptime(mes_atual, '%Y-%m') - timedelta(days=1)).strftime('%Y-%m')
            
            cursor.execute("""
                SELECT perfil, classe_ativo, banda_inferior, banda_neutra, banda_superior
                FROM carteira_recomendada 
                WHERE mes_referencia = %s AND perfil = %s
                ORDER BY classe_ativo
            """, (mes_anterior, perfil))
            
            carteira_anterior = cursor.fetchall()
            
            # Formatar carteira anterior para o prompt
            carteira_anterior_texto = ""
            if carteira_anterior:
                carteira_anterior_texto = "\nCarteira do Mês Anterior:\n"
                for carteira in carteira_anterior:
                    carteira_anterior_texto += f"- {carteira['classe_ativo']}: Banda Neutra {carteira['banda_neutra']}%\n"

            # Formatar carteiras atuais
            carteiras_texto = "\nCarteira Proposta:\n"
            for carteira in carteiras:
                carteiras_texto += f"- {carteira['classe_ativo']}: Banda Inferior {carteira['banda_inferior']}%, Banda Superior {carteira['banda_superior']}%\n"

            # Formatar avaliações
            avaliacoes_texto = ""
            for avaliacao in avaliacoes:
                avaliacoes_texto += f"- {avaliacao['classe_ativo']}: Nota {avaliacao['nota']} ({avaliacao['perspectiva']})\n"

            # Gerar prompt
            prompt = gerar_prompt(carteiras_texto, avaliacoes_texto, perfil, carteira_anterior_texto)

        except Error as e:
            print(f"Erro ao buscar carteira anterior: {str(e)}")
            # Continua mesmo sem a carteira anterior

        try:
            response = model.generate_content(prompt)
            # Extrair os valores e a explicação da resposta
            valores_match = re.search(r'\[VALORES\]\s*([\d\.,\s]+)', response.text)
            explicacao_match = re.search(r'\[EXPLICAÇÃO\]\s*(.*)', response.text, re.DOTALL)
            
            if not valores_match:
                raise ValueError("Não foi possível extrair os valores da resposta da IA")
            
            valores = [float(v.strip()) for v in valores_match.group(1).split(',')]
            explicacao = explicacao_match.group(1).strip() if explicacao_match else ""

            # Verificar se a soma dos valores é 100%
            if abs(sum(valores) - 100) > 0.01:
                raise ValueError("A soma das bandas neutras deve ser 100%")

            # Criar sugestões para cada carteira
            sugestoes = []
            for carteira, valor in zip(carteiras, valores):
                sugestoes.append({
                    'perfil': carteira['perfil'],
                    'classe_ativo': carteira['classe_ativo'],
                    'banda_neutra': valor
                })

            return jsonify({
                'sugestoes': sugestoes,
                'explicacao': explicacao
            }), 200

        except Exception as e:
            return jsonify({'error': f'Erro ao processar resposta da IA: {str(e)}'}), 500

    except Exception as e:
        return jsonify({'error': f'Erro ao processar sugestões da IA: {str(e)}'}), 500