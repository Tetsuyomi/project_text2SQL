import logging
import re
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
        
        return sql_text
    
    async def generate_sql(self, natural_language_query: str, rag_system, previous_sql: Optional[str] = None, previous_error: Optional[str] = None, attempt: Optional[int] = None) -> str:
        """Генерирует SQL запрос с использованием RAG системы для оптимизации промта."""
        try:
            # Используем RAG систему для получения релевантных частей схемы
            relevant_chunks = rag_system.retrieve_relevant_chunks(natural_language_query, top_k=5)
            allowed_schema = rag_system.get_allowed_schema()
            allowed_txt = "\n".join([f"{t}: {', '.join(cols)}" for t, cols in allowed_schema.items()]) if allowed_schema else ""
            join_hints = rag_system.get_join_hints()
            join_txt = "\n".join(join_hints) if join_hints else ""
            join_hint_block = f"\nПодсказки для JOIN:\n{join_txt}" if join_txt else ""
            
            # Соберем подсказки по примерам значений
            examples_block = ""
            try:
                seen_cols = set()
                for ch in (relevant_chunks or []):
                    meta = ch.get("metadata", {})
                    table = meta.get("table_name")
                    for col in (meta.get("columns") or []):
                        if (table, col) in seen_cols:
                            continue
                        seen_cols.add((table, col))
                        info = rag_system.column_values.get((table, col))
                        if not info:
                            continue
                        if info.get("examples"):
                            vals = ", ".join([str(v) for v in info["examples"][:10]])
                            examples_block += f"\n{table}.{col} примеры: {vals}"
                        elif info.get("range"):
                            r = info["range"]
                            examples_block += f"\n{table}.{col} диапазон: [{r.get('min')}, {r.get('max')}]"
                        elif info.get("datetime_range"):
                            dr = info["datetime_range"]
                            examples_block += f"\n{table}.{col} период: [{dr.get('start')}, {dr.get('end')}]"
            except Exception:
                examples_block = ""
            
            if relevant_chunks:
                # Строим оптимизированный промт с RAG
                prompt = rag_system.build_optimized_prompt(natural_language_query, relevant_chunks)
                prompt += "\nИспользуй только следующие существующие таблицы и столбцы (названия точные):\n" + allowed_txt
                if join_txt:
                    prompt += "\nПодсказки для JOIN (если уместно):\n" + join_txt
                if examples_block:
                    prompt += "\nПодсказки по значениям:\n" + examples_block
                prompt += "\nНе придумывай новые имена. Если поле не существует, не используй его."
                prompt += "\nДля JOIN используй только существующие связи между таблицами."
                prompt += "\nПри необходимости используй агрегаты вида COUNT(DISTINCT a.accident_number), SUM(a.died), COALESCE(.../NULLIF(...,0)::float,0)."
                prompt += "\nНе используй Markdown-разметку и не обрамляй ответ в ``` или теги языка."
                prompt += "\nВАЖНО: В таблице cars НЕТ поля 'district' или 'district_name'. Поле 'district_name' есть только в таблице 'accidents'."
                logger.info(f"Using RAG-optimized prompt with {len(relevant_chunks)} relevant chunks")
            else:
                # Fallback к старому методу
                prompt_parts = [
                    "Ты эксперт по генерации SQL-запросов. Создай корректный SQL-запрос для PostgreSQL.",
                    "",
                    "Доступные таблицы и столбцы (названия точные):",
                    allowed_txt,
                    join_hint_block if join_hint_block else "",
                    f"Подсказки по значениям: {examples_block}" if examples_block else "",
                    "",
                    "Запрос на естественном языке:",
                    natural_language_query
                ]
                prompt = "\n".join(prompt_parts)
                
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
                prompt += "\nВАЖНО: В таблице cars НЕТ поля 'district' или 'district_name'. Поле 'district_name' есть только в таблице 'accidents'."
                logger.info("Using fallback prompt method")

            logger.info(f"Generating SQL query for attempt {attempt}")
            response = self.client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[{"role": "user", "content": prompt}]
            )
            raw_sql = response.choices[0].message.content.strip()
            cleaned = self.sanitize_sql_query(raw_sql)
            normalized = self.normalize_identifiers(cleaned, allowed_schema)
            return normalized
            
        except Exception as e:
            logger.error(f"Error in RAG-based SQL generation: {str(e)}")
            # Fallback к старому методу в случае ошибки
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

            response = self.client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[{"role": "user", "content": prompt}]
            )
            raw_sql = response.choices[0].message.content.strip()
            cleaned = self.sanitize_sql_query(raw_sql)
            normalized = self.normalize_identifiers(cleaned, allowed_schema)
            return normalized
