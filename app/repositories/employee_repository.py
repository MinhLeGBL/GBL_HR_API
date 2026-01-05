from typing import Optional, Dict, Any, List

from app.database.connection import get_oracle_connection
from app.queries.employee_queries import EmployeeQueries


class EmployeeRepository:

    def __init__(self, db_connection):
        self.queries = EmployeeQueries()

    def execute(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        conn = get_oracle_connection()
        if not conn:
            raise Exception("Failed to connect to database")

        try:
            with conn.cursor() as cursor:
                if parameters:
                    cursor.execute(query, parameters)
                else:
                    cursor.execute(query)

                # Get column names
                columns = [desc[0] for desc in cursor.description]

                # Fetch all results and convert to list of dictionaries
                results = []
                for row in cursor.fetchall():
                    result_dict = dict(zip(columns, row))
                    results.append(result_dict)

                return results

        finally:
            conn.close()
        pass
