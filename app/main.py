from fastapi import FastAPI, Request, File, UploadFile, Form, HTTPException
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
from typing import Dict, Any, List, Optional
from .rag_system import DatabaseSchemaRAG
from .ddl_parser import DDLParser
from .sql_generator import SQLGenerator
from .database_manager import DatabaseManager

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Text-to-SQL Generator", version="2.0.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Инициализация компонентов
rag_system = DatabaseSchemaRAG()
ddl_parser = DDLParser()
sql_generator = SQLGenerator()
db_manager = DatabaseManager()

# Глобальное состояние приложения
app_state = {
    "schema_loaded": False,
    "ddl_loaded": False,
    "filters_loaded": False,
    "database_connected": False
}

@app.on_event("startup")
async def startup_event():
    """Инициализация приложения при запуске"""
    try:
        await db_manager.connect()
        app_state["database_connected"] = True
        logger.info("Database connection established")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        app_state["database_connected"] = False

@app.on_event("shutdown")
async def shutdown_event():
    """Очистка ресурсов при завершении"""
    await db_manager.disconnect()
    logger.info("Application shutdown complete")

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Главная страница приложения"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "app_state": app_state
    })

@app.post("/upload-ddl")
async def upload_ddl(ddl_file: UploadFile = File(...)):
    """Загрузка DDL файла с определением таблиц"""
    if not ddl_file.filename.endswith(".sql"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .sql")
    
    try:
        content = (await ddl_file.read()).decode("utf-8", errors="ignore")
        ddl_schema = ddl_parser.parse(content)
        
        # Загружаем схему в RAG систему
        rag_system.load_ddl_schema(ddl_schema)
        
        # Обновляем состояние приложения
        app_state["ddl_loaded"] = True
        app_state["schema_loaded"] = True
        
        logger.info(f"DDL uploaded successfully with {len(ddl_schema.get('tables', {}))} tables")
        
        return {
            "status": "success",
            "message": "DDL файл загружен успешно",
            "tables": list(ddl_schema.get("tables", {}).keys()),
            "tables_count": len(ddl_schema.get("tables", {}))
        }
        
    except Exception as e:
        logger.error(f"DDL upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки DDL: {str(e)}")

@app.post("/upload-filters")
async def upload_filters(filters_file: UploadFile = File(...)):
    """Загрузка JSON файла с фильтрами и примерами значений"""
    if not filters_file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение .json")
    
    try:
        content = (await filters_file.read()).decode("utf-8", errors="ignore")
        filters_data = json.loads(content)
        
        # Обрабатываем фильтры через RAG систему
        rag_system.ingest_response_values(filters_data)
        
        # Обновляем состояние приложения
        app_state["filters_loaded"] = True
        
        logger.info(f"Filters uploaded successfully")
        
        return {
            "status": "success",
            "message": "Фильтры загружены успешно",
            "filters_count": len(filters_data)
        }
        
    except Exception as e:
        logger.error(f"Filters upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки фильтров: {str(e)}")

@app.post("/query")
async def process_query(natural_language_query: str = Form(...)):
    """Обработка текстового запроса с генерацией SQL и выполнением"""
    if not app_state["schema_loaded"]:
        raise HTTPException(status_code=400, detail="Схема базы данных не загружена")
    
    if not app_state["database_connected"]:
        raise HTTPException(status_code=500, detail="Нет подключения к базе данных")
    
    try:
        # Генерируем SQL запрос
        sql_query = await sql_generator.generate_sql(
            natural_language_query=natural_language_query,
            rag_system=rag_system
        )
        
        # Выполняем запрос с логикой повторных попыток
        result = await db_manager.execute_with_retry(
            sql_query=sql_query,
            natural_language_query=natural_language_query,
            sql_generator=sql_generator,
            rag_system=rag_system
        )
        
        # Сохраняем результат
        await save_query_result(natural_language_query, sql_query, result)
        
        return {
            "status": "success",
            "sql_query": sql_query,
            "result": result
        }
        
    except Exception as e:
        logger.error(f"Query processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка обработки запроса: {str(e)}")

async def save_query_result(query: str, sql: str, result: Dict[str, Any]):
    """Сохранение результата запроса в JSON файл"""
    try:
        result_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "query": query,
            "sql_query": sql,
            "status": result.get("status", "unknown"),
            "data": result.get("data", []),
            "error": result.get("error", None),
            "attempts": result.get("attempts", [])
        }
        
        json_file_path = "query_results.json"
        
        # Загружаем существующие результаты
        if os.path.exists(json_file_path):
            with open(json_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
        else:
            data = []
        
        # Добавляем новый результат
        data.append(result_entry)
        
        # Сохраняем обновленный файл
        with open(json_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4, cls=DecimalEncoder)
            
        logger.info(f"Query result saved to {json_file_path}")
        
    except Exception as e:
        logger.error(f"Failed to save query result: {e}")

@app.get("/status")
async def get_status():
    """Получение статуса приложения"""
    return {
        "app_state": app_state,
        "rag_status": rag_system.get_status(),
        "database_status": await db_manager.get_status()
    }

@app.get("/health")
async def health_check():
    """Проверка здоровья приложения"""
    try:
        db_status = await db_manager.get_status()
        return {
            "status": "healthy",
            "database": db_status["connected"],
            "rag_system": rag_system.get_status()["ready"]
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
