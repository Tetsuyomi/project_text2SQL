from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import json
from openai import OpenAI
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.sql import text
from sqlalchemy.exc import DatabaseError, OperationalError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import os
import logging
import datetime
from decimal import Decimal

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Initialize LLM7 client
client = OpenAI(
    base_url="https://api.llm7.io/v1",
    api_key="unused",
    http_client=httpx.Client(timeout=30.0)
)

# Store schema globally (for simplicity)
db_schema = {}

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://readonly_user:readonly_user@db:5432/dtp-map-db")
engine = create_async_engine(DATABASE_URL, echo=True)

async def check_db_connection():
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection successful")
    except OperationalError as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise

def generate_sql_query(natural_language_query, schema, previous_sql=None, previous_error=None, attempt=None):
    schema_str = json.dumps(schema, indent=2)
    prompt = f"""
    Ты эксперт по генерации SQL-запросов. На основе схемы базы данных и запроса на естественном языке создайте корректный SQL-запрос для PostgreSQL.

    DDL или схема таблиц в базе данных:
    {schema_str}

    Запрос на естественном языке:
    {natural_language_query}
    """
    if previous_sql and previous_error:
        prompt += f"""
        Предыдущий SQL-запрос (попытка {attempt - 1}):
        {previous_sql}

        Ошибка от базы данных:
        {previous_error}

        Проанализируй ошибку и исправь SQL-запрос, чтобы он стал корректным. Убедись, что запрос безопасен и не выполняет деструктивные действия. Используй схему для проверки существующих столбцов и таблиц.
        """
    else:
        prompt += "Предоставь только SQL-запрос в виде обычного текста. Убедись, что он корректен для PostgreSQL. Не позволяй пользователю выполнять деструктивные действия (DROP, DELETE, UPDATE)."

    prompt += "\nВ ответе должен быть только SQL-запрос."

    logger.info(f"Generating SQL query for attempt {attempt}")
    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)  # Преобразуем Decimal в float для JSON
        return super().default(obj)

@retry(
    stop=stop_after_attempt(10),
    wait=wait_fixed(2),
    retry=retry_if_exception_type(DatabaseError)
)
async def execute_sql_query(sql_query, natural_language_query, max_attempts=10):
    if any(keyword in sql_query.lower() for keyword in ["drop", "delete", "update"]):
        logger.error("Destructive SQL operations are not allowed")
        return {"status": "error", "error": "Destructive SQL operations are not allowed"}

    previous_sql = sql_query
    previous_error = None
    sql_attempts = []

    for attempt in range(1, max_attempts + 1):
        async with AsyncSession(engine) as session:
            try:
                logger.info(f"Executing SQL query on attempt {attempt}: {sql_query}")
                result = await session.execute(text(sql_query))
                rows = result.fetchall()
                columns = result.keys()
                result_data = [dict(zip(columns, row)) for row in rows]
                logger.info(f"SQL query executed successfully on attempt {attempt}")
                return {"status": "success", "data": result_data, "sql_attempts": sql_attempts}
            except DatabaseError as e:
                logger.error(f"Database error on attempt {attempt}: {str(e)}")
                if attempt == max_attempts:
                    return {"status": "error", "error": str(e)} #, "sql_attempts": sql_attempts}
                previous_error = str(e)
                # Передаем предыдущий SQL и ошибку в generate_sql_query для доработки
                sql_query = generate_sql_query(
                    natural_language_query=natural_language_query,
                    schema=db_schema,
                    previous_sql=previous_sql,
                    previous_error=previous_error,
                    attempt=attempt + 1
                )
                sql_attempts.append({f"sql_query{attempt + 1}": sql_query})
                previous_sql = sql_query
                logger.info(f"Generated new SQL query for attempt {attempt + 1}: {sql_query}")
                await session.rollback()
                continue

@app.post("/upload-schema")
async def upload_schema(schema_file: UploadFile = File(...)):
    if not schema_file.filename.endswith(".json"):
        logger.error("Invalid file format: JSON file required")
        return {"error": "Please upload a JSON file"}, 400
    try:
        content = await schema_file.read()
        global db_schema
        db_schema = json.loads(content)
        logger.info("Schema uploaded successfully")
        return {"status": "Schema uploaded successfully"}
    except Exception as e:
        logger.error(f"Error uploading schema: {str(e)}")
        return {"error": str(e)}, 500

@app.post("/query")
async def query(natural_language_query: str = Form(...)):
    if not db_schema:
        logger.error("No schema uploaded")
        return {"error": "No schema uploaded"}, 400
    try:
        sql_query = generate_sql_query(natural_language_query, db_schema)
        logger.info(f"Initial SQL query generated: {sql_query}")
        result = await execute_sql_query(sql_query, natural_language_query)
        # Подготовка данных для записи в JSON
        result_entry = {
            "query": natural_language_query,
            "sql_query1": sql_query,
            "answer": json.dumps(result["data"], cls=DecimalEncoder) if result["status"] == "success" else "No data due to error",
        }
        if result["status"] == "error":
            result_entry["answer2"] = str(result["error"])
        # Добавление дополнительных попыток SQL
        if result.get("sql_attempts") and result["sql_attempts"]:  # Проверяем наличие и непустоту sql_attempts
            result_entry.update({k: v for d in result["sql_attempts"] for k, v in d.items()})
        # Сохранение в JSON-файл
        json_file_path = os.path.join(os.getcwd(), "query_results.json")
        try:
            if os.path.exists(json_file_path):
                with open(json_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not isinstance(data, list):
                        data = []
            else:
                data = []
            data.append({
                "timestamp": datetime.datetime.now().isoformat(),
                **result_entry
            })
            with open(json_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"Query result saved to {json_file_path}")
        except Exception as e:
            logger.error(f"Failed to save query result to JSON: {str(e)}")
        return {
            "sql_query": sql_query,
            "result": result
        }
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        return {"error": str(e)}, 500

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/healthcheck")
async def healthcheck():
    try:
        await check_db_connection()
        return {"status": "Database connection OK"}
    except Exception as e:
        logger.error(f"Healthcheck failed: {str(e)}")
        return {"status": "error", "error": str(e)}, 500

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)