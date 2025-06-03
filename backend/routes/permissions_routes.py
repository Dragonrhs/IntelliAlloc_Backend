from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from middleware.permissions import check_permission
from middleware.auth import token_required

permissions_bp = Blueprint('permissions', __name__)

# Rotas para gerenciar funcionalidades
@permissions_bp.route('/funcionalidades', methods=['GET'])
# @check_permission()  # Temporariamente removido para permitir acesso
def get_funcionalidades():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM funcionalidades ORDER BY nome")
    funcionalidades = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(funcionalidades)

@permissions_bp.route('/funcionalidades', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def create_funcionalidade():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO funcionalidades (nome, descricao, rota, metodo)
            VALUES (%s, %s, %s, %s)
        """, (data['nome'], data['descricao'], data['rota'], data['metodo']))
        conn.commit()
        return jsonify({'message': 'Funcionalidade criada com sucesso'}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

# Rotas para gerenciar cargos
@permissions_bp.route('/cargos', methods=['GET'])
# @check_permission()  # Temporariamente removido para permitir acesso
def get_cargos():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM cargos ORDER BY nome")
    cargos = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(cargos)

# Rotas para gerenciar permissões de cargos
@permissions_bp.route('/cargos/<int:cargo_id>/permissoes', methods=['GET'])
# @check_permission()  # Temporariamente removido para permitir acesso
def get_cargo_permissions(cargo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT f.*, pc.id as permissao_id
        FROM funcionalidades f
        LEFT JOIN permissoes_cargos pc ON f.id = pc.funcionalidade_id AND pc.cargo_id = %s
    """, (cargo_id,))
    
    permissoes = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(permissoes)

@permissions_bp.route('/cargos/<int:cargo_id>/permissoes', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def add_cargo_permission(cargo_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO permissoes_cargos (cargo_id, funcionalidade_id)
            VALUES (%s, %s)
        """, (cargo_id, data['funcionalidade_id']))
        conn.commit()
        return jsonify({'message': 'Permissão adicionada com sucesso'}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@permissions_bp.route('/cargos/<int:cargo_id>/permissoes/<int:funcionalidade_id>', methods=['DELETE'])
# @check_permission()  # Temporariamente removido para permitir acesso
def remove_cargo_permission(cargo_id, funcionalidade_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            DELETE FROM permissoes_cargos
            WHERE cargo_id = %s AND funcionalidade_id = %s
        """, (cargo_id, funcionalidade_id))
        conn.commit()
        return jsonify({'message': 'Permissão removida com sucesso'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@permissions_bp.route('/cargos/<int:cargo_id>/permissoes/batch', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def update_cargo_permissions_batch(cargo_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Primeiro, remover todas as permissões existentes para este cargo
        cursor.execute("DELETE FROM permissoes_cargos WHERE cargo_id = %s", (cargo_id,))
        
        # Depois, adicionar as novas permissões
        novas_permissoes = []
        if 'funcionalidades' in data and data['funcionalidades']:
            valores = [(cargo_id, funcionalidade_id) for funcionalidade_id in data['funcionalidades']]
            cursor.executemany("""
                INSERT INTO permissoes_cargos (cargo_id, funcionalidade_id)
                VALUES (%s, %s)
            """, valores)
            novas_permissoes = data['funcionalidades']
        
        # Buscar todos os usuários que possuem este cargo
        cursor.execute("SELECT id FROM user WHERE cargo_id = %s", (cargo_id,))
        usuarios = [row[0] for row in cursor.fetchall()]
        
        usuarios_atualizados = 0
        if usuarios:
            # Para cada usuário com este cargo
            for user_id in usuarios:
                # Remover todas as permissões existentes do usuário
                cursor.execute("DELETE FROM permissoes_usuarios WHERE user_id = %s", (user_id,))
                
                # Adicionar as novas permissões para o usuário
                for funcionalidade_id in novas_permissoes:
                    cursor.execute("""
                        INSERT INTO permissoes_usuarios (user_id, funcionalidade_id, permitido)
                        VALUES (%s, %s, TRUE)
                    """, (user_id, funcionalidade_id))
                usuarios_atualizados += 1
            
        conn.commit()
        return jsonify({
            'message': 'Permissões atualizadas com sucesso',
            'usuarios_atualizados': usuarios_atualizados
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

# Rotas para gerenciar permissões individuais de usuários
@permissions_bp.route('/usuarios/<int:user_id>/permissoes', methods=['GET'])
# @check_permission()  # Temporariamente removido para permitir acesso
def get_user_permissions(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT f.*, pu.permitido, pu.id as permissao_id
        FROM funcionalidades f
        LEFT JOIN permissoes_usuarios pu ON f.id = pu.funcionalidade_id AND pu.user_id = %s
    """, (user_id,))
    
    permissoes = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(permissoes)

@permissions_bp.route('/usuarios/<int:user_id>/permissoes', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def add_user_permission(user_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO permissoes_usuarios (user_id, funcionalidade_id, permitido)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE permitido = %s
        """, (user_id, data['funcionalidade_id'], data['permitido'], data['permitido']))
        conn.commit()
        return jsonify({'message': 'Permissão atualizada com sucesso'}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@permissions_bp.route('/usuarios/<int:user_id>/permissoes/<int:funcionalidade_id>', methods=['DELETE'])
# @check_permission()  # Temporariamente removido para permitir acesso
def remove_user_permission(user_id, funcionalidade_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            DELETE FROM permissoes_usuarios
            WHERE user_id = %s AND funcionalidade_id = %s
        """, (user_id, funcionalidade_id))
        conn.commit()
        return jsonify({'message': 'Permissão removida com sucesso'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@permissions_bp.route('/usuarios/<int:user_id>/permissoes/batch', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def update_user_permissions_batch(user_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        if 'permissoes' not in data:
            return jsonify({'message': 'Dados de permissões não fornecidos'}), 400
            
        # Primeiro, remover todas as permissões existentes para este usuário
        cursor.execute("DELETE FROM permissoes_usuarios WHERE user_id = %s", (user_id,))
        
        # Depois, adicionar as novas permissões
        if data['permissoes']:
            for permissao in data['permissoes']:
                cursor.execute("""
                    INSERT INTO permissoes_usuarios (user_id, funcionalidade_id, permitido)
                    VALUES (%s, %s, %s)
                """, (
                    user_id, 
                    permissao['funcionalidade_id'], 
                    permissao['permitido']
                ))
            
        conn.commit()
        return jsonify({'message': 'Permissões do usuário atualizadas com sucesso'}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

# Adicionar endpoint para auto-registrar a rota /cargos
@permissions_bp.route('/registrar-permissoes-iniciais', methods=['POST'])
# @check_permission()  # Temporariamente removido para permitir acesso
def registrar_permissoes_iniciais():
    """
    Endpoint para registrar permissões iniciais do sistema no banco de dados
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Registrar funcionalidades básicas se não existirem
        rotas_basicas = [
            {
                'nome': 'Listar Cargos',
                'descricao': 'Listar todos os cargos do sistema',
                'rota': '/cargos',
                'metodo': 'GET'
            },
            {
                'nome': 'Listar Funcionalidades',
                'descricao': 'Listar todas as funcionalidades do sistema',
                'rota': '/funcionalidades',
                'metodo': 'GET'
            },
            {
                'nome': 'Listar Usuários',
                'descricao': 'Listar todos os usuários do sistema',
                'rota': '/users',
                'metodo': 'GET'
            },
            {
                'nome': 'Gerenciar Permissões de Cargo',
                'descricao': 'Obter permissões de um cargo específico',
                'rota': '/cargos/<int:cargo_id>/permissoes',
                'metodo': 'GET'
            },
            {
                'nome': 'Adicionar Permissão de Cargo',
                'descricao': 'Adicionar permissão a um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes',
                'metodo': 'POST'
            },
            {
                'nome': 'Remover Permissão de Cargo',
                'descricao': 'Remover permissão de um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes/<int:funcionalidade_id>',
                'metodo': 'DELETE'
            },
            {
                'nome': 'Atualizar Permissões de Cargo em Lote',
                'descricao': 'Atualizar todas as permissões de um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes/batch',
                'metodo': 'POST'
            },
            {
                'nome': 'Gerenciar Permissões de Usuário',
                'descricao': 'Obter permissões de um usuário específico',
                'rota': '/usuarios/<int:user_id>/permissoes',
                'metodo': 'GET'
            },
            {
                'nome': 'Adicionar Permissão de Usuário',
                'descricao': 'Adicionar permissão a um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes',
                'metodo': 'POST'
            },
            {
                'nome': 'Remover Permissão de Usuário',
                'descricao': 'Remover permissão de um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes/<int:funcionalidade_id>',
                'metodo': 'DELETE'
            },
            {
                'nome': 'Atualizar Permissões de Usuário em Lote',
                'descricao': 'Atualizar todas as permissões de um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes/batch',
                'metodo': 'POST'
            }
        ]
        
        # Registrar cada funcionalidade
        for rota in rotas_basicas:
            cursor.execute("""
                INSERT IGNORE INTO funcionalidades (nome, descricao, rota, metodo)
                VALUES (%s, %s, %s, %s)
            """, (rota['nome'], rota['descricao'], rota['rota'], rota['metodo']))
        
        # Buscar o cargo de Admin
        cursor.execute("SELECT id FROM cargos WHERE nome = 'Admin'")
        admin_cargo_id = cursor.fetchone()[0]
        
        # Buscar todas as funcionalidades
        cursor.execute("SELECT id FROM funcionalidades")
        funcionalidades_ids = [row[0] for row in cursor.fetchall()]
        
        # Dar todas as permissões ao Admin
        for func_id in funcionalidades_ids:
            cursor.execute("""
                INSERT IGNORE INTO permissoes_cargos (cargo_id, funcionalidade_id)
                VALUES (%s, %s)
            """, (admin_cargo_id, func_id))
        
        conn.commit()
        return jsonify({
            'message': 'Permissões iniciais registradas com sucesso',
            'funcionalidades_registradas': len(rotas_basicas),
            'permissoes_admin': len(funcionalidades_ids)
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

# Rota temporária para registrar permissões sem autenticação
@permissions_bp.route('/registrar-permissoes-sem-autenticacao', methods=['POST'])
def registrar_permissoes_sem_autenticacao():
    """
    Endpoint temporário para registrar permissões iniciais do sistema sem exigir autenticação
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Registrar funcionalidades básicas se não existirem
        rotas_basicas = [
            {
                'nome': 'Listar Cargos',
                'descricao': 'Listar todos os cargos do sistema',
                'rota': '/cargos',
                'metodo': 'GET'
            },
            {
                'nome': 'Listar Funcionalidades',
                'descricao': 'Listar todas as funcionalidades do sistema',
                'rota': '/funcionalidades',
                'metodo': 'GET'
            },
            {
                'nome': 'Listar Usuários',
                'descricao': 'Listar todos os usuários do sistema',
                'rota': '/users',
                'metodo': 'GET'
            },
            {
                'nome': 'Gerenciar Permissões de Cargo',
                'descricao': 'Obter permissões de um cargo específico',
                'rota': '/cargos/<int:cargo_id>/permissoes',
                'metodo': 'GET'
            },
            {
                'nome': 'Adicionar Permissão de Cargo',
                'descricao': 'Adicionar permissão a um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes',
                'metodo': 'POST'
            },
            {
                'nome': 'Remover Permissão de Cargo',
                'descricao': 'Remover permissão de um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes/<int:funcionalidade_id>',
                'metodo': 'DELETE'
            },
            {
                'nome': 'Atualizar Permissões de Cargo em Lote',
                'descricao': 'Atualizar todas as permissões de um cargo',
                'rota': '/cargos/<int:cargo_id>/permissoes/batch',
                'metodo': 'POST'
            },
            {
                'nome': 'Gerenciar Permissões de Usuário',
                'descricao': 'Obter permissões de um usuário específico',
                'rota': '/usuarios/<int:user_id>/permissoes',
                'metodo': 'GET'
            },
            {
                'nome': 'Adicionar Permissão de Usuário',
                'descricao': 'Adicionar permissão a um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes',
                'metodo': 'POST'
            },
            {
                'nome': 'Remover Permissão de Usuário',
                'descricao': 'Remover permissão de um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes/<int:funcionalidade_id>',
                'metodo': 'DELETE'
            },
            {
                'nome': 'Atualizar Permissões de Usuário em Lote',
                'descricao': 'Atualizar todas as permissões de um usuário',
                'rota': '/usuarios/<int:user_id>/permissoes/batch',
                'metodo': 'POST'
            },
            {
                'nome': 'Registrar Permissões Iniciais',
                'descricao': 'Registrar permissões iniciais do sistema',
                'rota': '/registrar-permissoes-iniciais',
                'metodo': 'POST'
            },
            {
                'nome': 'Alterar Cargo de Usuário',
                'descricao': 'Alterar o cargo de um usuário',
                'rota': '/users/<int:user_id>/role',
                'metodo': 'PUT'
            }
        ]
        
        # Registrar cada funcionalidade
        for rota in rotas_basicas:
            cursor.execute("""
                INSERT IGNORE INTO funcionalidades (nome, descricao, rota, metodo)
                VALUES (%s, %s, %s, %s)
            """, (rota['nome'], rota['descricao'], rota['rota'], rota['metodo']))
        
        # Buscar o cargo de Admin
        cursor.execute("SELECT id FROM cargos WHERE nome = 'Admin'")
        admin_cargo_id = cursor.fetchone()[0]
        
        # Buscar todas as funcionalidades
        cursor.execute("SELECT id FROM funcionalidades")
        funcionalidades_ids = [row[0] for row in cursor.fetchall()]
        
        # Dar todas as permissões ao Admin
        for func_id in funcionalidades_ids:
            cursor.execute("""
                INSERT IGNORE INTO permissoes_cargos (cargo_id, funcionalidade_id)
                VALUES (%s, %s)
            """, (admin_cargo_id, func_id))
        
        conn.commit()
        return jsonify({
            'message': 'Permissões iniciais registradas com sucesso',
            'funcionalidades_registradas': len(rotas_basicas),
            'permissoes_admin': len(funcionalidades_ids)
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close()

@permissions_bp.route('/me/permissoes', methods=['GET'])
@token_required
def get_my_permissions(current_user):
    """
    Retorna todas as permissões efetivas do usuário logado,
    considerando sobrescrita individual e permissões do cargo
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Primeiro, buscar todas as funcionalidades do sistema
        cursor.execute("""
            SELECT id, rota, metodo
            FROM funcionalidades
        """)
        todas_funcionalidades = cursor.fetchall()
        
        # Depois, buscar permissões individuais do usuário
        cursor.execute("""
            SELECT f.rota, f.metodo, pu.permitido
            FROM permissoes_usuarios pu
            JOIN funcionalidades f ON pu.funcionalidade_id = f.id
            WHERE pu.user_id = %s
        """, (current_user['id'],))
        permissoes_usuario = {f"{p['rota']}:{p['metodo']}": p['permitido'] for p in cursor.fetchall()}
        
        # Por fim, buscar permissões do cargo do usuário
        cursor.execute("""
            SELECT f.rota, f.metodo
            FROM permissoes_cargos pc
            JOIN funcionalidades f ON pc.funcionalidade_id = f.id
            WHERE pc.cargo_id = %s
        """, (current_user['cargo_id'],))
        permissoes_cargo = {f"{p['rota']}:{p['metodo']}" for p in cursor.fetchall()}
        
        # Montar resultado final
        permissoes_efetivas = []
        for func in todas_funcionalidades:
            chave = f"{func['rota']}:{func['metodo']}"
            
            # Se existe permissão individual, usa ela
            if chave in permissoes_usuario:
                permitido = permissoes_usuario[chave]
            # Se não, verifica se tem permissão do cargo
            else:
                permitido = chave in permissoes_cargo
            
            permissoes_efetivas.append({
                'rota': func['rota'],
                'metodo': func['metodo'],
                'permitido': permitido
            })
        
        return jsonify(permissoes_efetivas)
        
    except Exception as e:
        return jsonify({'message': str(e)}), 400
    finally:
        cursor.close()
        conn.close() 