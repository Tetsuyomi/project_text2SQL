import logging
from typing import Dict, Any, List
import sqlglot
from sqlglot.expressions import Create, ColumnDef, ForeignKey, Table, Identifier, Reference

logger = logging.getLogger(__name__)

class DDLParser:
    """
    Парсит DDL (CREATE TABLE ...) в нормализованную схему {tables: {...}} и извлекает внешние ключи.
    Поддерживается базовый синтаксис PostgreSQL.
    """
    
    def parse(self, ddl_sql: str) -> Dict[str, Any]:
        logger.info("=" * 60)
        logger.info("ПАРСИНГ DDL")
        logger.info("=" * 60)
        logger.info(f"📝 DDL содержимое (первые 500 символов): {ddl_sql[:500]}...")
        
        try:
            parsed = sqlglot.parse(ddl_sql, read="postgres")
            logger.info(f"✅ SQLGlot успешно распарсил DDL")
            logger.info(f"📊 Найдено выражений: {len(parsed)}")
        except Exception as e:
            logger.error(f"❌ Ошибка парсинга DDL: {e}")
            return {"tables": {}, "foreign_keys": []}
        
        tables: Dict[str, Any] = {}
        foreign_keys: List[Dict[str, Any]] = []
        
        for i, stmt in enumerate(parsed):
            logger.info(f"🔍 Обработка выражения {i+1}: {type(stmt).__name__}")
            
            if isinstance(stmt, Create) and stmt.kind == "TABLE":
                table_name = self._table_name(stmt.this)
                logger.info(f"📋 Найдена таблица: {table_name}")
                
                if not table_name:
                    logger.warning(f"⚠️ Не удалось извлечь имя таблицы из {stmt.this}")
                    continue
                
                tables.setdefault(table_name, {"columns": {}, "primary_key": [], "foreign_keys": []})
                # columns and constraints
                logger.info(f"   🔍 Обработка колонок таблицы {table_name}...")
                
                # Колонки находятся в stmt.this.expressions, а не в stmt.expressions
                table_expressions = stmt.this.expressions if hasattr(stmt.this, 'expressions') else []
                logger.info(f"   📊 Всего выражений в таблице: {len(table_expressions)}")
                
                for i, expression in enumerate(table_expressions):
                    logger.info(f"     🔍 Выражение {i+1}: {type(expression).__name__} = {expression}")
                    
                    if isinstance(expression, ColumnDef):
                        col_name = self._id_name(expression.this)  # expression.name -> expression.this
                        col_type = (expression.kind.sql(dialect="postgres") if expression.kind else "TEXT").upper()
                        tables[table_name]["columns"][col_name] = col_type
                        logger.info(f"     ✅ Колонка: {col_name} ({col_type})")
                        
                        # primary key (column-level)
                        if expression.constraints:
                            for c in expression.constraints:
                                constraint_type = type(c.kind).__name__ if c.kind else None
                                logger.info(f"     🔍 Constraint: {constraint_type}")
                                if constraint_type and "PRIMARYKEY" in constraint_type.upper():
                                    tables[table_name]["primary_key"].append(col_name)
                                    logger.info(f"     🔑 Primary Key (column-level): {col_name}")
                    
                    elif isinstance(expression, ForeignKey):
                        # table-level FK
                        cols = [self._id_name(i) for i in (expression.expressions or [])]
                        ref_table = self._table_name(expression.reference)
                        ref_cols = [self._id_name(i) for i in (getattr(expression, "reference_columns", []) or [])]
                        fk = {"table": table_name, "columns": cols, "ref_table": ref_table, "ref_columns": ref_cols}
                        foreign_keys.append(fk)
                        tables[table_name]["foreign_keys"].append({"columns": cols, "ref_table": ref_table, "ref_columns": ref_cols})
                        logger.info(f"     🔗 Внешний ключ: {cols} -> {ref_table}.{ref_cols}")
                    
                    else:
                        logger.info(f"     ⚠️ Неизвестный тип выражения: {type(expression).__name__}")
                        # другие table-level constraints: PRIMARY KEY(...)
                        kind = getattr(expression, "kind", None)
                        if kind:
                            kind_type = type(kind).__name__
                            logger.info(f"     🔍 Table constraint kind: {kind_type}")
                            if "PRIMARYKEY" in kind_type.upper():
                                cols = [self._id_name(i) for i in (expression.expressions or [])]
                                tables[table_name]["primary_key"].extend(cols)
                                logger.info(f"     🔑 Primary Key (table-level): {cols}")
        
        logger.info(f"📊 РЕЗУЛЬТАТ ПАРСИНГА:")
        logger.info(f"   Найдено таблиц: {len(tables)}")
        for table_name, table_info in tables.items():
            columns_count = len(table_info.get("columns", {}))
            pk_count = len(table_info.get("primary_key", []))
            fk_count = len(table_info.get("foreign_keys", []))
            logger.info(f"   📋 {table_name}: {columns_count} колонок, {pk_count} PK, {fk_count} FK")
        
        logger.info(f"   Внешних ключей: {len(foreign_keys)}")
        logger.info("=" * 60)
        
        return {"tables": tables, "foreign_keys": foreign_keys}
    
    def _table_name(self, node) -> str:
        logger.info(f"🔍 Извлечение имени таблицы из: {type(node).__name__} = {node}")
        
        if isinstance(node, Table):
            result = self._id_name(node.this)
            logger.info(f"   ✅ Table: {result}")
            return result
        if isinstance(node, Reference):
            result = self._id_name(node.this)
            logger.info(f"   ✅ Reference: {result}")
            return result
        
        # Для объекта Schema извлекаем имя таблицы
        if hasattr(node, 'this') and hasattr(node.this, 'this'):
            result = self._id_name(node.this.this)
            logger.info(f"   ✅ Schema.this.this: {result}")
            return result
        
        # Попробуем извлечь имя напрямую
        if hasattr(node, 'this'):
            result = self._id_name(node.this)
            logger.info(f"   ✅ Direct this: {result}")
            return result
        
        # Попробуем строковое представление
        result = str(node)
        logger.info(f"   ✅ String: {result}")
        return result
    
    def _id_name(self, node) -> str:
        logger.info(f"   🔍 Извлечение имени из: {type(node).__name__} = {node}")
        
        if isinstance(node, Identifier):
            result = str(node.this or "")
            logger.info(f"   ✅ Identifier: {result}")
            return result
        
        # Попробуем извлечь this атрибут
        if hasattr(node, 'this'):
            result = str(node.this or "")
            logger.info(f"   ✅ This attribute: {result}")
            return result
        
        # Строковое представление
        result = str(node or "")
        logger.info(f"   ✅ String: {result}")
        return result
