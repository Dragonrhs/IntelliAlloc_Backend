from utils.db import get_db_connection
from mysql.connector import Error

def log_client_action(user_id, client_id, action_type, client_name):
    try:
        connection = get_db_connection()
        if connection is None:
            print("Erro: Não foi possível conectar ao banco para registrar ação no histórico")
            return False

        cursor = connection.cursor()
        details = f"Cliente {client_name} foi {'inserido' if action_type == 'INSERT' else 'editado' if action_type == 'UPDATE' else 'excluído'}"
        query = """
            INSERT INTO client_history (user_id, client_id, action_type, details)
            VALUES (%s, %s, %s, %s)
        """
        print(f"Registrando ação no histórico: user_id={user_id}, client_id={client_id}, action_type={action_type}, details={details}")
        cursor.execute(query, (user_id, client_id, action_type, details))
        connection.commit()
        print("Ação registrada com sucesso no histórico")
        return True

    except Error as e:
        print(f"Erro ao registrar ação no histórico: {str(e)}")
        return False
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()