class EmployeeQueries:

    EMPLOYEE_INFO = """
        SELECT
            emp.SID as EMPLOYEE_SID,
            cust.UDF4_STRING as EMPLOYEE_CODE,
            cust.FIRST_NAME as EMPLOYEE_NAME,
            emp.USER_NAME as EMPLOYEE_LOGIN,
            CASE WHEN emp.USER_ACTIVE = 0 THEN 'INACTIVE' ELSE 'ACTIVE' END as EMPLOYEE_STATUS,
            s.STORE_CODE as EMPLOYEE_STORE
        FROM EMPLOYEE emp
        JOIN CUSTOMER cust ON emp.CUST_SID = cust.SID
        JOIN STORE s ON emp.BASE_STORE_SID = s.SID
    """