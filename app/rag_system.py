import json
import logging
from typing import List, Dict, Any, Optional, Tuple
import chromadb
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re

logger = logging.getLogger(__name__)

class DatabaseSchemaRAG:
    """
    RAG система для работы со схемами баз данных.
    Использует TF-IDF для создания эмбеддингов и поиска релевантных частей схемы.
    """
    
    def __init__(self, persist_directory: str = "./chroma_db"):
        self.persist_directory = persist_directory
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = None
        self.schema_chunks = []
        self.schema_metadata = {}
        self.tfidf_vectorizer = None
        self.tfidf_matrix = None
        self.normalized_schema: Dict[str, Any] = {}
        self.ddl_schema: Dict[str, Any] = {}
        self.column_values: Dict[Tuple[str, str], Dict[str, Any]] = {}
        
    def _convert_filters_to_tables(self, raw_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Конвертирует структуру формата *_filters в стандартный формат {"tables": {...}}."""
        tables: Dict[str, Dict[str, Any]] = {}
        for key, value in raw_schema.items():
            if not key.endswith("_filters") or not isinstance(value, list):
                continue
            table_name = key[:-8] if len(key) > 8 else key
            if table_name not in tables:
                tables[table_name] = {"columns": {}}
            for item in value:
                try:
                    filter_type = item.get("filter_type")
                    f = item.get("filter", {})
                    field = f.get("field")
                    if not field:
                        continue
                    
                    # Определяем тип по filter_type
                    inferred_type = "TEXT"
                    if filter_type == "datetime-range-filter":
                        inferred_type = "TIMESTAMP WITH TIME ZONE"
                    elif filter_type == "value-range-filter":
                        min_v = f.get("min_value")
                        max_v = f.get("max_value")
                        if isinstance(min_v, float) or isinstance(max_v, float):
                            inferred_type = "DOUBLE PRECISION"
                        else:
                            inferred_type = "INTEGER"
                    elif filter_type == "enum-filter":
                        inferred_type = "TEXT"
                    
                    tables[table_name]["columns"].setdefault(field, inferred_type)
                except Exception:
                    continue
        return {"tables": tables}
        
    def chunk_schema(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Разбивает схему БД на логические чанки для лучшего понимания."""
        self.normalized_schema = schema if schema.get("tables") else self._convert_filters_to_tables(schema)
        tables_obj = self.normalized_schema.get("tables", {})
        
        chunks = []
        chunk_id = 0
        
        # Обрабатываем каждую таблицу
        for table_name, table_info in tables_obj.items():
            # Чанк с основной информацией о таблице
            table_chunk = {
                'id': f"chunk_{chunk_id}",
                'type': 'table_overview',
                'table_name': table_name,
                'content': f"Таблица: {table_name}",
                'metadata': {
                    'table_name': table_name,
                    'chunk_type': 'table_overview',
                    'columns_count': len(table_info.get('columns', {}))
                }
            }
            chunks.append(table_chunk)
            chunk_id += 1
            
            # Чанки с группами столбцов
            columns = table_info.get('columns', {})
            if columns:
                column_groups = self._group_columns(columns)
                
                for group_name, group_columns in column_groups.items():
                    group_content = f"Таблица {table_name} - {group_name}:\n"
                    for col_name, col_type in group_columns.items():
                        group_content += f"  {col_name}: {col_type}\n"
                    
                    column_chunk = {
                        'id': f"chunk_{chunk_id}",
                        'type': 'column_group',
                        'table_name': table_name,
                        'content': group_content.strip(),
                        'metadata': {
                            'table_name': table_name,
                            'chunk_type': 'column_group',
                            'group_name': group_name,
                            'columns': list(group_columns.keys())
                        }
                    }
                    chunks.append(column_chunk)
                    chunk_id += 1
                
                # Чанк со всеми столбцами таблицы
                all_columns_content = f"Таблица {table_name} - все столбцы:\n"
                for col_name, col_type in columns.items():
                    all_columns_content += f"  {col_name}: {col_type}\n"
                
                all_columns_chunk = {
                    'id': f"chunk_{chunk_id}",
                    'type': 'all_columns',
                    'table_name': table_name,
                    'content': all_columns_content.strip(),
                    'metadata': {
                        'table_name': table_name,
                        'chunk_type': 'all_columns',
                        'columns': list(columns.keys())
                    }
                }
                chunks.append(all_columns_chunk)
                chunk_id += 1
        
        # Чанк с общей структурой БД
        if tables_obj:
            tables_list = list(tables_obj.keys())
            structure_chunk = {
                'id': f"chunk_{chunk_id}",
                'type': 'database_structure',
                'table_name': 'general',
                'content': f"Структура базы данных:\nТаблицы: {', '.join(tables_list)}",
                'metadata': {
                    'chunk_type': 'database_structure',
                    'tables': tables_list
                }
            }
            chunks.append(structure_chunk)
        
        self.schema_chunks = chunks
        return chunks

    def load_ddl_schema(self, ddl_schema: Dict[str, Any]):
        """Загружает схему из DDL."""
        self.ddl_schema = ddl_schema or {}

    def ingest_response_values(self, response_json: Dict[str, Any]):
        """Извлекает справочные значения для колонок из *_filters."""
        for key, arr in (response_json or {}).items():
            if not key.endswith("_filters") or not isinstance(arr, list):
                continue
            table = key[:-8]
            for item in arr:
                f = (item or {}).get("filter", {})
                field = f.get("field")
                if not field:
                    continue
                rec = {}
                if item.get("filter_type") == "enum-filter":
                    vals = f.get("values")
                    if isinstance(vals, list):
                        filtered_vals = [v for v in vals if v is not None and v != "" and str(v).strip()]
                        if filtered_vals:
                            rec["examples"] = filtered_vals[:50]
                elif item.get("filter_type") == "value-range-filter":
                    min_val = f.get("min_value")
                    max_val = f.get("max_value")
                    if min_val is not None or max_val is not None:
                        rec["range"] = {"min": min_val, "max": max_val}
                elif item.get("filter_type") == "datetime-range-filter":
                    start_dt = f.get("start_datetime")
                    end_dt = f.get("end_datetime")
                    if start_dt or end_dt:
                        rec["datetime_range"] = {"start": start_dt, "end": end_dt}
                if rec:
                    self.column_values[(table, field)] = rec

    def get_allowed_schema(self) -> Dict[str, List[str]]:
        """Возвращает разрешенные таблицы и столбцы."""
        if self.ddl_schema.get("tables"):
            return {t: list(info.get("columns", {}).keys()) for t, info in self.ddl_schema["tables"].items()}
        
        allowed: Dict[str, List[str]] = {}
        tables = (self.normalized_schema or {}).get("tables", {})
        for tname, tinfo in tables.items():
            allowed[tname] = list((tinfo or {}).get("columns", {}).keys())
        return allowed

    def get_join_hints(self, max_hints: int = 10) -> List[str]:
        """Возвращает подсказки для JOIN операций."""
        tables = (self.ddl_schema or {}).get("tables", {})
        hints: List[str] = []
        
        # Извлекаем FK из DDL
        for t, info in tables.items():
            for fk in info.get("foreign_keys", []) or []:
                lhs = ", ".join([f"{t}.{c}" for c in fk.get("columns", [])])
                rhs = ", ".join([f"{fk.get('ref_table')}.{c}" for c in fk.get("ref_columns", [])])
                if lhs and rhs:
                    hints.append(f"{lhs} = {rhs}")
        
        # Если FK не найдены, добавляем известные связи
        if not hints:
            known_relationships = [
                "cars.accident_number = accidents.accident_number",
                "participants.car_number = cars.car_number"
            ]
            hints.extend(known_relationships)
        
        if hints:
            return list(dict.fromkeys(hints))[:max_hints]
        return []

    def _group_columns(self, columns: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """Группирует столбцы по логическим категориям."""
        groups = {
            'Основные идентификаторы': {},
            'Временные метки': {},
            'Географические данные': {},
            'Числовые показатели': {},
            'Текстовые описания': {},
            'Массивы и списки': {},
            'Прочие': {}
        }
        
        for col_name, col_type in columns.items():
            col_lower = col_name.lower()
            col_type_lower = col_type.lower()
            
            if any(keyword in col_lower for keyword in ['id', 'number', 'code']):
                groups['Основные идентификаторы'][col_name] = col_type
            elif any(keyword in col_lower for keyword in ['date', 'time', 'created', 'updated']):
                groups['Временные метки'][col_name] = col_type
            elif any(keyword in col_lower for keyword in ['lat', 'lon', 'location', 'place', 'street', 'road']):
                groups['Географические данные'][col_name] = col_type
            elif any(keyword in col_lower for keyword in ['count', 'number', 'amount', 'level', 'width', 'length']):
                groups['Числовые показатели'][col_name] = col_type
            elif '[]' in col_type_lower:
                groups['Массивы и списки'][col_name] = col_type
            elif 'text' in col_type_lower:
                groups['Текстовые описания'][col_name] = col_type
            else:
                groups['Прочие'][col_name] = col_type
        
        return {k: v for k, v in groups.items() if v}
    
    def create_embeddings(self, chunks: List[Dict[str, Any]]) -> List[List[float]]:
        """Создает TF-IDF эмбеддинги для всех чанков схемы."""
        texts = [chunk['content'] for chunk in chunks]
        if not texts or all((t or '').strip() == '' for t in texts):
            logger.warning("No chunk texts available for embeddings")
            return []
        
        try:
            self.tfidf_vectorizer = TfidfVectorizer(
                max_features=1000, 
                stop_words='english',
                ngram_range=(1, 2)
            )
            
            self.tfidf_matrix = self.tfidf_vectorizer.fit_transform(texts)
            embeddings = self.tfidf_matrix.toarray().tolist()
            logger.info(f"Created TF-IDF embeddings for {len(chunks)} chunks")
            return embeddings
            
        except Exception as e:
            logger.error(f"TF-IDF embedding failed: {e}")
            import random
            random.seed(42)
            return [[random.random() for _ in range(100)] for _ in texts]
    
    def store_in_vector_db(self, chunks: List[Dict[str, Any]], embeddings: List[List[float]]):
        """Сохраняет чанки и их эмбеддинги в векторную БД."""
        try:
            if not chunks:
                logger.warning("No chunks to store in vector database")
                return
            
            collection_name = "database_schema"
            try:
                existing = self.client.get_collection(name=collection_name)
                self.client.delete_collection(name=collection_name)
                logger.info(f"Deleted existing collection: {collection_name}")
            except Exception:
                pass
            
            self.collection = self.client.create_collection(
                name=collection_name,
                metadata={"description": "Database schema chunks for RAG system"}
            )
            logger.info(f"Created new collection: {collection_name}")
            
            ids = [chunk['id'] for chunk in chunks]
            documents = [chunk['content'] for chunk in chunks]
            metadatas = [chunk['metadata'] for chunk in chunks]
            
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings if embeddings else None
            )
            
            logger.info(f"Successfully stored {len(chunks)} chunks in vector database")
            
        except Exception as e:
            logger.error(f"Error storing in vector database: {str(e)}")
            raise
    
    def analyze_query(self, query: str) -> Dict[str, Any]:
        """Анализирует пользовательский запрос."""
        query_lower = query.lower()
        analysis = {
            'required_tables': set(),
            'required_fields': set(),
            'query_type': 'unknown',
            'keywords': []
        }
        
        # Определяем тип запроса
        if any(word in query_lower for word in ['количество', 'count', 'сколько', 'число']):
            analysis['query_type'] = 'count'
        elif any(word in query_lower for word in ['среднее', 'average', 'avg', 'средний']):
            analysis['query_type'] = 'average'
        elif any(word in query_lower for word in ['сумма', 'sum', 'итого']):
            analysis['query_type'] = 'sum'
        elif any(word in query_lower for word in ['максимум', 'maximum', 'max', 'минимум', 'minimum', 'min']):
            analysis['query_type'] = 'aggregation'
        else:
            analysis['query_type'] = 'select'
        
        # Извлекаем ключевые слова
        keywords = re.findall(r'\b\w+\b', query_lower)
        analysis['keywords'] = [kw for kw in keywords if len(kw) > 2]
        
        return analysis
    
    def retrieve_relevant_chunks(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Извлекает наиболее релевантные чанки схемы для заданного запроса."""
        if not self.collection:
            logger.error("Vector database collection not initialized")
            try:
                if self.schema_chunks:
                    logger.info("Attempting to reinitialize vector database collection")
                    embeddings = self.create_embeddings(self.schema_chunks)
                    self.store_in_vector_db(self.schema_chunks, embeddings)
                    if self.collection:
                        logger.info("Successfully reinitialized vector database collection")
                        return self._retrieve_with_collection(query, top_k)
            except Exception as e:
                logger.error(f"Failed to reinitialize vector database: {e}")
            return self._fallback_retrieval(query, top_k)
        
        return self._retrieve_with_collection(query, top_k)
    
    def _retrieve_with_collection(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Извлекает чанки используя инициализированную коллекцию."""
        try:
            if self.tfidf_vectorizer is not None:
                query_vector = self.tfidf_vectorizer.transform([query])
                similarities = cosine_similarity(query_vector, self.tfidf_matrix).flatten()
                top_indices = similarities.argsort()[-top_k:][::-1]
                
                relevant_chunks = []
                for idx in top_indices:
                    chunk = self.schema_chunks[idx]
                    chunk_info = {
                        'id': chunk['id'],
                        'content': chunk['content'],
                        'metadata': chunk['metadata'],
                        'similarity_score': float(similarities[idx])
                    }
                    relevant_chunks.append(chunk_info)
                
                logger.info(f"Retrieved {len(relevant_chunks)} relevant chunks using TF-IDF")
                return relevant_chunks
            else:
                return self._fallback_retrieval(query, top_k)
                
        except Exception as e:
            logger.error(f"Error retrieving relevant chunks: {str(e)}")
            return self._fallback_retrieval(query, top_k)
    
    def _fallback_retrieval(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Fallback метод поиска релевантных чанков через ключевые слова."""
        if not self.schema_chunks:
            return []
        
        query_lower = query.lower()
        query_words = set(re.findall(r'\b\w+\b', query_lower))
        
        scored_chunks = []
        for chunk in self.schema_chunks:
            content_lower = chunk['content'].lower()
            score = 0
            
            for word in query_words:
                if len(word) > 2 and word in content_lower:
                    score += 1
            
            if score > 0:
                scored_chunks.append({
                    'id': chunk['id'],
                    'content': chunk['content'],
                    'metadata': chunk['metadata'],
                    'similarity_score': score / len(query_words)
                })
        
        scored_chunks.sort(key=lambda x: x['similarity_score'], reverse=True)
        logger.info(f"Using fallback retrieval for {len(scored_chunks)} chunks")
        return scored_chunks[:top_k]
    
    def build_optimized_prompt(self, query: str, relevant_chunks: List[Dict[str, Any]]) -> str:
        """Строит оптимизированный промт с релевантными частями схемы."""
        if not relevant_chunks:
            return f"""
            Ты эксперт по генерации SQL-запросов. Создай корректный SQL-запрос для PostgreSQL.

            Запрос на естественном языке:
            {query}

            Примечание: Схема базы данных не загружена или не найдена. 
            Создай SQL-запрос на основе понимания типичных структур БД.
            """
        
        query_analysis = self.analyze_query(query)
        
        context_parts = []
        
        structure_chunks = [c for c in relevant_chunks if c['metadata'].get('chunk_type') == 'database_structure']
        if structure_chunks:
            context_parts.append(structure_chunks[0]['content'])
        
        table_chunks = [c for c in relevant_chunks if c['metadata'].get('chunk_type') in ['table_overview', 'all_columns']]
        for chunk in table_chunks[:3]:
            context_parts.append(chunk['content'])
        
        column_group_chunks = [c for c in relevant_chunks if c['metadata'].get('chunk_type') == 'column_group']
        for chunk in column_group_chunks[:2]:
            context_parts.append(chunk['content'])
        
        context = "\n\n".join(context_parts)
        
        prompt = f"""
        Ты эксперт по генерации SQL-запросов. На основе предоставленной схемы базы данных и запроса на естественном языке создай корректный SQL-запрос для PostgreSQL.

        Схема базы данных (релевантные части):
        {context}

        Запрос на естественном языке:
        {query}

        Анализ запроса:
        - Тип запроса: {query_analysis['query_type']}
        - Ключевые слова: {', '.join(query_analysis['keywords'])}

        Создай SQL-запрос, который:
        1. Использует только существующие таблицы и столбцы из схемы
        2. Корректен для PostgreSQL
        3. Безопасен (не выполняет деструктивные действия)
        4. Оптимизирован для указанного типа запроса

        В ответе должен быть только SQL-запрос.
        """
        
        return prompt
    
    def process_schema(self, schema: Dict[str, Any]):
        """Обрабатывает схему БД: разбивает на чанки, создает эмбеддинги и сохраняет в векторную БД."""
        logger.info("Processing database schema for RAG system...")
        
        chunks = self.chunk_schema(schema)
        logger.info(f"Created {len(chunks)} schema chunks")
        
        embeddings = self.create_embeddings(chunks)
        logger.info(f"Created embeddings for {len(embeddings)} chunks")
        
        self.store_in_vector_db(chunks, embeddings)
        logger.info("Schema successfully processed and stored in RAG system")
        
        return chunks
    
    def get_schema_summary(self) -> Dict[str, Any]:
        """Возвращает краткую сводку по схеме БД."""
        if not self.schema_chunks:
            return {"error": "Schema not processed yet"}
        
        tables = set()
        total_columns = 0
        
        for chunk in self.schema_chunks:
            if chunk['metadata'].get('table_name') and chunk['metadata']['table_name'] != 'general':
                tables.add(chunk['metadata']['table_name'])
            if chunk['metadata'].get('columns_count'):
                total_columns += chunk['metadata']['columns_count']
        
        return {
            "total_tables": len(tables),
            "total_columns": total_columns,
            "tables": list(tables),
            "chunks_count": len(self.schema_chunks)
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Возвращает статус RAG системы."""
        return {
            "ready": len(self.schema_chunks) > 0,
            "chunks_count": len(self.schema_chunks),
            "schema_loaded": bool(self.ddl_schema or self.normalized_schema),
            "filters_loaded": len(self.column_values) > 0,
            "vector_db_ready": self.collection is not None
        }
