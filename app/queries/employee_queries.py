class EmployeeQueries:

    EMPLOYEE_INFO = """
        SELECT
            e.SID as EMPLOYEE_SID,
            e.UDF4_STRING as EMPLOYEE_CODE,
            e.FULL_NAME as EMPLOYEE_NAME,
            e.EMPL_NAME as EMPLOYEE_LOGIN,
            case when e.ACTIVE = 0 then 'INACTIVE' else 'ACTIVE' end as EMPLOYEE_STATUS,
            e.STORE_CODE as EMPLOYEE_STORE
        FROM EMPLOYEE_LIST_V e
    """