# GBL HR API - Project Structure

## Overview
This HR API layer receives requests from a frontend UI, processes them through business logic, and executes queries against an Oracle database.

## Request Flow
```
Frontend UI → API Routes → Service Layer → Repository → SQL Queries → Oracle DB
                ↓              ↓             ↓            ↓
            Validation   Business Logic  Data Access   SQL
```

## Directory Structure

```
GBL_HR_API/
├── app/
│   ├── __init__.py
│   ├── main.py                          # Flask application factory
│   │
│   ├── api/                             # API Layer
│   │   ├── v1/
│   │   │   ├── routes/                  # HTTP endpoint definitions
│   │   │   │   ├── employee_routes.py   # Employee endpoints
│   │   │   │   ├── sales_routes.py      # Sales report endpoints
│   │   │   │   └── health_routes.py     # Health check endpoints
│   │   │   └── schemas/                 # Request/Response validation
│   │   │       ├── employee_schemas.py
│   │   │       └── sales_schemas.py
│   │
│   ├── services/                        # Business Logic Layer
│   │   ├── employee_service.py          # Employee business logic
│   │   └── sales_service.py             # Sales business logic
│   │
│   ├── repositories/                    # Data Access Layer
│   │   ├── employee_repository.py       # Employee data access
│   │   └── sales_repository.py          # Sales data access
│   │
│   ├── queries/                         # SQL Queries
│   │   ├── employee_queries.py          # Employee SQL
│   │   └── sales_queries.py             # Sales SQL
│   │
│   ├── models/                          # Domain Models
│   ├── database/                        # Database Connection
│   │   └── connection.py
│   ├── core/                            # Core Configuration
│   └── utils/                           # Utilities
│       ├── validators.py
│       ├── formatters.py
│       └── decorators.py
│
├── config/                              # Configuration files
├── tests/                               # Test suite
├── docs/                                # Documentation
├── logs/                                # Application logs
├── app.py                               # Application entry point
└── requirements.txt
```

## Layer Responsibilities

### 1. API Layer (`app/api/v1/routes/`)
- **Purpose**: Handles HTTP requests from frontend
- **Responsibilities**:
  - Route definition and URL mapping
  - Request validation
  - Response formatting
  - HTTP status code handling
- **Example**: `employee_routes.py`, `sales_routes.py`

### 2. Schemas (`app/api/v1/schemas/`)
- **Purpose**: Request/Response validation and formatting
- **Responsibilities**:
  - Validate incoming request data
  - Format response data for API
  - Define data contracts
- **Example**: `SalesReportRequest.validate()`

### 3. Service Layer (`app/services/`)
- **Purpose**: Business logic and orchestration
- **Responsibilities**:
  - Implement business rules
  - Coordinate between multiple repositories
  - Data transformation and calculations
  - Business validation
- **Example**: `EmployeeService`, `SalesService`

### 4. Repository Layer (`app/repositories/`)
- **Purpose**: Data access abstraction
- **Responsibilities**:
  - Execute database queries
  - Handle database connections
  - Convert database results to dictionaries
  - Database error handling
- **Example**: `EmployeeRepository`, `RetailSalesRepository`

### 5. Queries (`app/queries/`)
- **Purpose**: SQL query definitions
- **Responsibilities**:
  - Store all SQL queries
  - Maintain query templates
  - Document query parameters
- **Example**: `EmployeeQueries.EMPLOYEE_INFO`

## API Endpoints

### Health Check
- `GET /api/v1/health` - API health status
- `GET /api/v1/health/database` - Database connection status

### Employees
- `GET /api/v1/employees/` - Get all employees
- `GET /api/v1/employees/<employee_code>` - Get employee by code
- `GET /api/v1/employees/store/<store_code>` - Get employees by store

### Sales Reports
- `POST /api/v1/sales/reports/detailed` - Detailed sales report
- `POST /api/v1/sales/reports/store-summary` - Store sales summary
- `POST /api/v1/sales/reports/customer-analysis` - Customer analysis

## Usage Examples

### Starting the Application
```bash
python app.py
```

### Making API Requests

**Get all employees:**
```bash
curl http://localhost:5000/api/v1/employees/
```

**Get detailed sales report:**
```bash
curl -X POST http://localhost:5000/api/v1/sales/reports/detailed \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-01 00:00:00",
    "end_date": "2024-12-31 23:59:59"
  }'
```

## Development Guidelines

### Adding a New Feature

1. **Create SQL Query** in `app/queries/`
2. **Add Repository Method** in `app/repositories/`
3. **Implement Service Logic** in `app/services/`
4. **Create API Schema** in `app/api/v1/schemas/`
5. **Add API Route** in `app/api/v1/routes/`
6. **Register Blueprint** in `app/main.py`

### Code Organization Principles

- **Separation of Concerns**: Each layer has a single responsibility
- **Dependency Direction**: API → Service → Repository → Queries
- **No Layer Skipping**: Always go through the service layer
- **Error Handling**: Handle errors at appropriate layers

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_employee_service.py

# Run with coverage
pytest --cov=app tests/
```

## Environment Variables

Create a `.env` file based on `.env.example`:
```
ORACLE_HOST=your_host
ORACLE_PORT=1521
ORACLE_SERVICE=your_service
ORACLE_USER=your_user
ORACLE_PASSWORD=your_password
```

## Next Steps

1. Add authentication/authorization middleware
2. Implement request rate limiting
3. Add caching layer (Redis)
4. Implement logging and monitoring
5. Add API documentation (Swagger/OpenAPI)
