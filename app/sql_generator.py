import logging
import re
import json
from typing import Dict, Any, List, Optional
from openai import OpenAI
import httpx
from difflib import get_close_matches

logger = logging.getLogger(__name__)

class SQLGenerator:
    """
    Генератор SQL запросов с использованием LLM и RAG системы.
    """
    
    def __init__(self):
        # Инициализация LLM клиента
        self.client = OpenAI(
            base_url="https://api.llm7.io/v1",
            api_key="unused",
            http_client=httpx.Client(timeout=30.0)
        )
    
    def sanitize_sql_query(self, sql_text: str) -> str:
        """Очищает SQL от Markdown и лишнего текста."""
        if not sql_text:
            return sql_text
        s = sql_text.strip()
        
        # Если присутствует блок ```sql ... ``` — извлекаем содержимое
        fence_match = re.search(r"```(?:sql)?\s*(.*?)```", s, re.IGNORECASE | re.DOTALL)
        if fence_match:
            s = fence_match.group(1).strip()
        
        # Удаляем одиночные обратные кавычки, если вдруг остались
        s = s.replace("```", "").strip()
        
        # Убираем префикс 'sql' строкой, если модель его добавила
        if s.lower().startswith("sql\n"):
            s = s[4:].strip()
        
        return s
    
    def normalize_identifiers(self, sql_text: str, allowed: dict) -> str:
        """Нормализует имена к допустимым (nearest match)."""
        if not sql_text or not allowed:
            return sql_text
        
        # Плоский список столбцов с квалификацией table.column
        table_to_cols = allowed
        all_tables = set(table_to_cols.keys())
        all_cols = set()
        for t, cols in table_to_cols.items():
            all_cols.update(cols)
        
        # Специальные замены для известных проблем
        special_replacements = {
            'car_id': 'accident_number',
            'id': 'accident_number',
            'region': 'district_name',
            'district': 'district_name',
            # НЕ заменяем count_accident на accidents, так как count_accident - это колонка в таблице concentrations
        }
        
        # Заменяем часто встречающиеся придуманные имена на ближайшие
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql_text)
        replacement_map = {}
        for tok in tokens:
            if tok.lower() in {"select","from","join","on","where","group","by","order","asc","desc","as","left","right","inner","outer","and","or","count","sum","avg","min","max"}:
                continue
            
            # Специальные замены
            if tok in special_replacements:
                replacement_map[tok] = special_replacements[tok]
                continue
                
            # Если это таблица
            match_table = get_close_matches(tok, list(all_tables), n=1, cutoff=0.6)
            if match_table and tok not in all_tables:
                replacement_map[tok] = match_table[0]
                continue
            # Если это столбец
            match_col = get_close_matches(tok, list(all_cols), n=1, cutoff=0.6)
            if match_col and tok not in all_cols:
                replacement_map[tok] = match_col[0]
        
        # Применяем замены целыми словами
        def repl(m):
            word = m.group(0)
            return replacement_map.get(word, word)
        if replacement_map:
            sql_text = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", repl, sql_text)
        
        # Исправляем неправильные JOIN
        sql_text = re.sub(r'(\w+)\.car_id\s*=\s*(\w+)\.id', r'\1.accident_number = \2.accident_number', sql_text, flags=re.IGNORECASE)
        
        # Исправляем неправильные поля
        sql_text = re.sub(r'(\w+)\.region', r'\1.district_name', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'cars\.district', r'accidents.district_name', sql_text, flags=re.IGNORECASE)
        
        # Исправляем придуманные таблицы
        sql_text = re.sub(r'\baccident_count\b', 'accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\baccident_table\b', 'accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\baccident_data\b', 'accidents', sql_text, flags=re.IGNORECASE)
        
        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: count_accident как таблица должна быть заменена на accidents
        # Но count_accident как колонка в concentrations должна остаться
        # Проверяем контекст: если count_accident используется в FROM/JOIN - это таблица
        sql_text = re.sub(r'\bFROM\s+count_accident\b', 'FROM accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\bJOIN\s+count_accident\b', 'JOIN accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\bUPDATE\s+count_accident\b', 'UPDATE accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\bDELETE\s+FROM\s+count_accident\b', 'DELETE FROM accidents', sql_text, flags=re.IGNORECASE)
        sql_text = re.sub(r'\bINSERT\s+INTO\s+count_accident\b', 'INSERT INTO accidents', sql_text, flags=re.IGNORECASE)
        
        return sql_text
    
    async def generate_sql(self, natural_language_query: str, rag_system, previous_sql: Optional[str] = None, previous_error: Optional[str] = None, attempt: Optional[int] = None) -> str:
        """Генерирует SQL запрос с использованием двухэтапного подхода."""
        logger.info("=" * 60)
        logger.info("ГЕНЕРАЦИЯ SQL ЗАПРОСА (ДВУХЭТАПНЫЙ ПОДХОД)")
        logger.info("=" * 60)
        logger.info(f"🔍 Запрос: {natural_language_query}")
        logger.info(f"📊 Попытка: {attempt}")
        logger.info("=" * 60)
        if previous_sql and previous_error:
            logger.info(f"🔄 Исправление предыдущего SQL")
            logger.info(f"   Предыдущий SQL: {previous_sql[:100]}...")
            logger.info(f"   Ошибка БД: {previous_error[:100]}...")
        logger.info("=" * 60)
        
        try:
            # Получаем полную схему базы данных
            logger.info("📋 Получение полной схемы БД...")
            allowed_schema = rag_system.get_allowed_schema()
            allowed_txt = "\n".join([f"{t}: {', '.join(cols)}" for t, cols in allowed_schema.items()]) if allowed_schema else ""
            logger.info(f"✅ Полная схема получена: {len(allowed_schema)} таблиц")
            
            # ЭТАП 1: Анализ запроса и определение нужных таблиц/столбцов
            logger.info("🔄 ЭТАП 1: Анализ запроса и определение нужных таблиц/столбцов")
            required_tables, required_columns = await self._analyze_query_requirements(
                natural_language_query, allowed_schema, rag_system, previous_sql, previous_error, attempt
            )
            
            logger.info(f"📊 Определены нужные таблицы: {list(required_tables)}")
            logger.info(f"📊 Определены нужные столбцы: {list(required_columns)}")
            
            # ЭТАП 2: Генерация SQL на основе только нужных таблиц/столбцов
            logger.info("🔄 ЭТАП 2: Генерация SQL на основе нужных таблиц/столбцов")
            sql_query = await self._generate_sql_from_requirements(
                natural_language_query, required_tables, required_columns, allowed_schema, 
                rag_system, previous_sql, previous_error, attempt
            )
            
            logger.info("✅ SQL запрос сгенерирован успешно")
            logger.info("=" * 60)
            return sql_query
            
        except Exception as e:
            logger.error(f"Error in two-stage SQL generation: {str(e)}")
            # Fallback к старому методу в случае ошибки
            return await self._fallback_generation(natural_language_query, rag_system, previous_sql, previous_error, attempt)
            
        except Exception as e:
            logger.error(f"Error in two-stage SQL generation: {str(e)}")
            # Fallback к старому методу в случае ошибки
            return await self._fallback_generation(natural_language_query, rag_system, previous_sql, previous_error, attempt)
    
    async def _analyze_query_requirements(self, query: str, allowed_schema: dict, rag_system, 
                                        previous_sql: Optional[str] = None, previous_error: Optional[str] = None, 
                                        attempt: Optional[int] = None) -> tuple[set, set]:
        """ЭТАП 1: Анализирует запрос и определяет нужные таблицы и столбцы."""
        logger.info("🔍 Анализ запроса для определения нужных таблиц и столбцов...")
        
        # Получаем подсказки для JOIN
        join_hints = rag_system.get_join_hints()
        join_txt = "\n".join(join_hints) if join_hints else ""
        
        # Строим промпт для анализа
        allowed_txt = "\n".join([f"{t}: {', '.join(cols)}" for t, cols in allowed_schema.items()])
        
        analysis_prompt = f"""
        Ты эксперт по анализу SQL-запросов. Проанализируй запрос на естественном языке и определи, какие таблицы и столбцы нужны для его выполнения.

        ПОЛНАЯ СХЕМА БАЗЫ ДАННЫХ:
        {allowed_txt}

        Подсказки для JOIN (если уместно):
        {join_txt}

        Запрос на естественном языке:
        {query}

        КРИТИЧЕСКИ ВАЖНО: 'count_accident' - это КОЛОНКА в таблице 'concentrations', а НЕ отдельная таблица.
        Если нужна таблица с данными о ДТП - используй 'accidents'.
        Если нужна таблица с данными о машинах - используй 'cars'.
        Если нужна таблица с данными об участниках - используй 'participants'.
        Если нужна таблица с концентрациями ДТП - используй 'concentrations'.

        Проанализируй запрос и верни ТОЛЬКО JSON в следующем формате:
        {{
            "required_tables": ["список", "нужных", "таблиц"],
            "required_columns": ["список", "нужных", "столбцов"]
        }}

        ВАЖНО:
        - Включай только те таблицы, которые действительно нужны для запроса
        - Включай только те столбцы, которые нужны для SELECT, WHERE, GROUP BY, ORDER BY
        - Не включай технические столбцы типа id, created_at, updated_at, если они не нужны
        - Учитывай связи между таблицами для JOIN
        """
        
        if previous_sql and previous_error:
            analysis_prompt += f"""

            ПРЕДЫДУЩАЯ ОШИБКА:
            SQL: {previous_sql}
            Ошибка: {previous_error}
            
            Учти эту ошибку при анализе и убедись, что выбранные таблицы и столбцы корректны.
            """
        
        logger.info("🤖 Отправка запроса на анализ к LLM...")
        response = self.client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=[{"role": "user", "content": analysis_prompt}]
        )
        
        analysis_result = response.choices[0].message.content.strip()
        logger.info(f"📝 Результат анализа: {analysis_result[:200]}...")
        
        # Парсим JSON результат
        try:
            analysis_data = json.loads(analysis_result)
            required_tables = set(analysis_data.get("required_tables", []))
            required_columns = set(analysis_data.get("required_columns", []))
            
            # Валидируем результаты
            valid_tables = set(allowed_schema.keys())
            all_valid_columns = set()
            for table, cols in allowed_schema.items():
                all_valid_columns.update(cols)
            
            # Фильтруем только валидные таблицы и столбцы
            required_tables = required_tables.intersection(valid_tables)
            required_columns = required_columns.intersection(all_valid_columns)
            
            logger.info(f"✅ Анализ завершен: {len(required_tables)} таблиц, {len(required_columns)} столбцов")
            return required_tables, required_columns
            
        except Exception as e:
            logger.error(f"Ошибка парсинга анализа: {e}")
            # Fallback: используем RAG для определения релевантных таблиц
            relevant_chunks = rag_system.retrieve_relevant_chunks(query, top_k=3)
            fallback_tables = set()
            fallback_columns = set()
            
            for chunk in relevant_chunks:
                table_name = chunk.get('metadata', {}).get('table_name')
                if table_name and table_name in allowed_schema:
                    fallback_tables.add(table_name)
                    columns = chunk.get('metadata', {}).get('columns', [])
                    fallback_columns.update(columns)
            
            logger.info(f"🔄 Fallback анализ: {len(fallback_tables)} таблиц, {len(fallback_columns)} столбцов")
            return fallback_tables, fallback_columns
    
    async def _generate_sql_from_requirements(self, query: str, required_tables: set, required_columns: set, 
                                            allowed_schema: dict, rag_system, previous_sql: Optional[str] = None, 
                                            previous_error: Optional[str] = None, attempt: Optional[int] = None) -> str:
        """ЭТАП 2: Генерирует SQL на основе определенных таблиц и столбцов."""
        logger.info("🔍 Генерация SQL на основе определенных требований...")
        
        # Строим схему только с нужными таблицами и столбцами
        focused_schema = {}
        for table in required_tables:
            if table in allowed_schema:
                table_columns = allowed_schema[table]
                # Включаем только нужные столбцы для этой таблицы
                focused_columns = [col for col in table_columns if col in required_columns]
                # Добавляем все столбцы таблицы, если нужны хотя бы некоторые
                if focused_columns:
                    focused_schema[table] = table_columns
        
        focused_txt = "\n".join([f"{t}: {', '.join(cols)}" for t, cols in focused_schema.items()])
        
        # Получаем подсказки для JOIN только для нужных таблиц
        join_hints = rag_system.get_join_hints()
        relevant_joins = [hint for hint in join_hints if any(table in hint for table in required_tables)]
        join_txt = "\n".join(relevant_joins) if relevant_joins else ""
        
        # Строим промпт для генерации SQL
        sql_prompt = f"""
        Ты эксперт по генерации SQL-запросов для PostgreSQL. Создай корректный SQL-запрос.

        НУЖНЫЕ ТАБЛИЦЫ И СТОЛБЦЫ (только те, что нужны для запроса):
        {focused_txt}

        Подсказки для JOIN (только для нужных таблиц):
        {join_txt}

        Запрос на естественном языке:
        {query}

        КРИТИЧЕСКИ ВАЖНО: Используй ТОЛЬКО указанные выше таблицы и столбцы.
        НЕ ПРИДУМЫВАЙ новые таблицы или столбцы.
        
        КРИТИЧЕСКИ ВАЖНО: 'count_accident' - это КОЛОНКА в таблице 'concentrations', а НЕ отдельная таблица.
        НЕ используй 'count_accident' в FROM или JOIN - это НЕ таблица!
        
        Для JOIN используй только существующие связи между таблицами.
        При необходимости используй агрегаты вида COUNT(DISTINCT a.accident_number), SUM(a.died), COALESCE(.../NULLIF(...,0)::float,0).
        
        В ответе должен быть только SQL-запрос без дополнительных комментариев.
        Не используй Markdown-разметку и не обрамляй ответ в ``` или теги языка.
        """
        
        if previous_sql and previous_error:
            sql_prompt += f"""

            ПРЕДЫДУЩАЯ ОШИБКА:
            SQL: {previous_sql}
            Ошибка: {previous_error}
            
            Исправь ошибку, используя только указанные выше таблицы и столбцы.
            """
        
        logger.info("🤖 Отправка запроса на генерацию SQL к LLM...")
        response = self.client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=[{"role": "user", "content": sql_prompt}]
        )
        
        raw_sql = response.choices[0].message.content.strip()
        logger.info(f"📝 Сырой SQL от LLM: {raw_sql[:100]}...")
        
        logger.info("🧹 Очистка SQL...")
        cleaned = self.sanitize_sql_query(raw_sql)
        logger.info(f"🧹 Очищенный SQL: {cleaned[:100]}...")
        
        logger.info("🔧 Нормализация идентификаторов...")
        normalized = self.normalize_identifiers(cleaned, focused_schema)
        logger.info(f"🔧 Финальный SQL: {normalized[:100]}...")
        
        return normalized
    
    async def _fallback_generation(self, natural_language_query: str, rag_system, 
                                 previous_sql: Optional[str] = None, previous_error: Optional[str] = None, 
                                 attempt: Optional[int] = None) -> str:
        """Fallback к старому методу генерации SQL."""
        logger.info("🔄 Использование fallback метода генерации SQL...")
        
        allowed_schema = rag_system.get_allowed_schema()
        allowed_txt = "\n".join([f"{t}: {', '.join(cols)}" for t, cols in allowed_schema.items()]) if allowed_schema else ""
        join_hints = rag_system.get_join_hints()
        join_txt = "\n".join(join_hints) if join_hints else ""
        join_hint_block = f"\nПодсказки для JOIN:\n{join_txt}" if join_txt else ""
        
        prompt = f"""
        Ты эксперт по генерации SQL-запросов. Создай корректный SQL-запрос для PostgreSQL.

        Доступные таблицы и столбцы (названия точные):
        {allowed_txt}{join_hint_block}

        Запрос на естественном языке:
        {natural_language_query}
        """
        
        if previous_sql and previous_error:
            prompt += f"""
            Предыдущий SQL-запрос (попытка {attempt - 1}):
            {previous_sql}

            Ошибка от базы данных:
            {previous_error}

            Проанализируй ошибку и исправь SQL-запрос, используя только доступные таблицы и столбцы.
            """
        else:
            prompt += "Предоставь только SQL-запрос в виде обычного текста. Используй только доступные таблицы и столбцы."
            prompt += "\nДля JOIN используй только существующие связи между таблицами."

        prompt += "\nПри необходимости используй агрегаты вида COUNT(DISTINCT a.accident_number), SUM(a.died), COALESCE(.../NULLIF(...,0)::float,0)."
        prompt += "\nВ ответе должен быть только SQL-запрос. Не используй Markdown-разметку и не обрамляй ответ в ``` или теги языка."
        prompt += "\nВАЖНО: Используй только существующие поля. В таблице accidents НЕТ поля 'region', используй 'district_name'."
        prompt += "\nВАЖНО: В таблице cars НЕТ поля 'district' или 'district_name'. Поле 'district_name' есть только в таблице 'accidents'."
        prompt += "\nКРИТИЧЕСКИ ВАЖНО: 'count_accident' - это КОЛОНКА в таблице 'concentrations', а НЕ отдельная таблица."
        prompt += "\nНЕ используй 'count_accident' в FROM или JOIN - это НЕ таблица!"
        prompt += "\nДля данных о ДТП используй таблицу 'accidents'."

        response = self.client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=[{"role": "user", "content": prompt}]
        )
        raw_sql = response.choices[0].message.content.strip()
        cleaned = self.sanitize_sql_query(raw_sql)
        normalized = self.normalize_identifiers(cleaned, allowed_schema)
        return normalized
