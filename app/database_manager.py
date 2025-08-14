import logging
import os
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.sql import text
from sqlalchemy.exc import DatabaseError, OperationalError
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

logger = logging.getLogger(__name__)

class DatabaseManager:
    """
    Менеджер для работы с базой данных PostgreSQL.
    Обеспечивает подключение, выполнение запросов и логику повторных попыток.
    """
    
    def __init__(self):
        self.engine = None
        self.connected = False
        self.database_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://readonly_user:readonly_user@db:5432/dtp-map-db")
    
    async def connect(self):
        """Устанавливает подключение к базе данных."""
        try:
            self.engine = create_async_engine(self.database_url, echo=True)
            # Проверяем подключение
            async with AsyncSession(self.engine) as session:
                await session.execute(text("SELECT 1"))
            self.connected = True
            logger.info("Database connection established successfully")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            self.connected = False
            raise
    
    async def disconnect(self):
        """Закрывает подключение к базе данных."""
        if self.engine:
            await self.engine.dispose()
            self.connected = False
            logger.info("Database connection closed")
    
    async def check_connection(self):
        """Проверяет активность подключения к базе данных."""
        if not self.engine:
            return False
        
        try:
            async with AsyncSession(self.engine) as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            self.connected = False
            return False
    
    def is_destructive_query(self, sql_query: str) -> bool:
        """Проверяет, является ли запрос деструктивным."""
        destructive_keywords = [
            "drop", "delete", "update", "insert", "create", "alter", 
            "truncate", "replace", "merge", "upsert"
        ]
        query_lower = sql_query.lower()
        return any(keyword in query_lower for keyword in destructive_keywords)
    
    @retry(
        stop=stop_after_attempt(10),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(DatabaseError)
    )
    async def execute_query(self, sql_query: str) -> Dict[str, Any]:
        """Выполняет SQL запрос с базовой логикой повторных попыток."""
        if self.is_destructive_query(sql_query):
            logger.error("Destructive SQL operations are not allowed")
            return {
                "status": "error", 
                "error": "Destructive SQL operations are not allowed"
            }
        
        try:
            async with AsyncSession(self.engine) as session:
                logger.info(f"Executing SQL query: {sql_query}")
                result = await session.execute(text(sql_query))
                rows = result.fetchall()
                columns = result.keys()
                result_data = [dict(zip(columns, row)) for row in rows]
                
                logger.info(f"SQL query executed successfully, returned {len(result_data)} rows")
                return {
                    "status": "success",
                    "data": result_data,
                    "rows_count": len(result_data)
                }
                
        except DatabaseError as e:
            logger.error(f"Database error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during query execution: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def execute_with_retry(self, sql_query: str, natural_language_query: str, sql_generator, rag_system, max_attempts: int = 10) -> Dict[str, Any]:
        """
        Выполняет SQL запрос с расширенной логикой повторных попыток.
        При ошибке генерирует новый SQL запрос и повторяет попытку.
        """
        if self.is_destructive_query(sql_query):
            logger.error("Destructive SQL operations are not allowed")
            return {
                "status": "error", 
                "error": "Destructive SQL operations are not allowed"
            }
        
        previous_sql = sql_query
        previous_error = None
        attempts = []
        
        for attempt_num in range(1, max_attempts + 1):
            try:
                logger.info(f"Executing SQL query (attempt {attempt_num}): {previous_sql}")
                
                async with AsyncSession(self.engine) as session:
                    result = await session.execute(text(previous_sql))
                    rows = result.fetchall()
                    columns = result.keys()
                    result_data = [dict(zip(columns, row)) for row in rows]
                    
                    logger.info(f"SQL query executed successfully on attempt {attempt_num}")
                    
                    return {
                        "status": "success",
                        "data": result_data,
                        "rows_count": len(result_data),
                        "attempts": attempts,
                        "final_sql": previous_sql
                    }
                    
            except DatabaseError as e:
                error_msg = str(e)
                logger.error(f"Database error on attempt {attempt_num}: {error_msg}")
                
                # Сохраняем информацию о попытке
                attempts.append({
                    "attempt": attempt_num,
                    "sql": previous_sql,
                    "error": error_msg
                })
                
                if attempt_num == max_attempts:
                    logger.error(f"Max attempts reached ({max_attempts}). Returning last error.")
                    return {
                        "status": "error",
                        "error": error_msg,
                        "attempts": attempts,
                        "final_sql": previous_sql
                    }
                
                # Генерируем новый SQL запрос на основе ошибки
                try:
                    previous_error = error_msg
                    new_sql = await sql_generator.generate_sql(
                        natural_language_query=natural_language_query,
                        rag_system=rag_system,
                        previous_sql=previous_sql,
                        previous_error=previous_error,
                        attempt=attempt_num + 1
                    )
                    
                    # Очищаем новый SQL
                    cleaned_sql = sql_generator.sanitize_sql_query(new_sql)
                    previous_sql = cleaned_sql
                    
                    logger.info(f"Generated new SQL query for attempt {attempt_num + 1}: {cleaned_sql}")
                    
                except Exception as gen_error:
                    logger.error(f"Failed to generate new SQL for attempt {attempt_num + 1}: {gen_error}")
                    return {
                        "status": "error",
                        "error": f"Failed to generate corrected SQL: {gen_error}",
                        "attempts": attempts,
                        "final_sql": previous_sql
                    }
                
                await session.rollback()
                continue
                
            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt_num}: {str(e)}")
                return {
                    "status": "error",
                    "error": str(e),
                    "attempts": attempts,
                    "final_sql": previous_sql
                }
    
    async def get_status(self) -> Dict[str, Any]:
        """Возвращает статус подключения к базе данных."""
        connection_ok = await self.check_connection()
        return {
            "connected": connection_ok,
            "database_url": self.database_url,
            "engine_initialized": self.engine is not None
        }
    
    async def test_connection(self) -> Dict[str, Any]:
        """Тестирует подключение к базе данных."""
        try:
            if not self.engine:
                return {"status": "error", "message": "Engine not initialized"}
            
            async with AsyncSession(self.engine) as session:
                result = await session.execute(text("SELECT version()"))
                version = result.scalar()
                
                return {
                    "status": "success",
                    "message": "Database connection test successful",
                    "version": version
                }
                
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return {
                "status": "error",
                "message": f"Database connection test failed: {str(e)}"
            }
