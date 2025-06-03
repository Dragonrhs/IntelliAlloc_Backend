from functools import wraps
from flask import request, jsonify
from utils.db import get_db_connection
from middleware.auth import token_required

def check_permission():
    def decorator(f):
        @wraps(f)
        @token_required
        def decorated_function(*args, **kwargs):
            current_user = kwargs.get('current_user')
            if not current_user:
                return jsonify({'message': 'Usuário não autenticado'}), 401

            # Obter a rota e o método atual
            current_route = request.path
            current_method = request.method

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            
            try:
                # Primeiro, verificar permissão individual do usuário
                cursor.execute("""
                    SELECT pu.permitido
                    FROM permissoes_usuarios pu
                    JOIN funcionalidades f ON pu.funcionalidade_id = f.id
                    WHERE pu.user_id = %s AND f.rota = %s AND f.metodo = %s
                """, (current_user['id'], current_route, current_method))
                
                user_permission = cursor.fetchone()
                
                if user_permission is not None:
                    # Se existe permissão individual, usar ela
                    if not user_permission['permitido']:
                        return jsonify({'message': 'Acesso negado'}), 403
                else:
                    # Se não existe permissão individual, verificar permissão do cargo
                    cursor.execute("""
                        SELECT 1
                        FROM permissoes_cargos pc
                        JOIN funcionalidades f ON pc.funcionalidade_id = f.id
                        WHERE pc.cargo_id = %s AND f.rota = %s AND f.metodo = %s
                    """, (current_user['cargo_id'], current_route, current_method))
                    
                    role_permission = cursor.fetchone()
                    
                    if not role_permission:
                        return jsonify({'message': 'Acesso negado'}), 403
                
                return f(*args, **kwargs)
                
            except Exception as e:
                return jsonify({'message': str(e)}), 400
            finally:
                cursor.close()
                conn.close()
                
        return decorated_function
    return decorator 