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
        parsed = sqlglot.parse(ddl_sql, read="postgres")
        tables: Dict[str, Any] = {}
        foreign_keys: List[Dict[str, Any]] = []
        
        for stmt in parsed:
            if isinstance(stmt, Create) and stmt.kind == "TABLE":
                table_name = self._table_name(stmt.this)
                if not table_name:
                    continue
                tables.setdefault(table_name, {"columns": {}, "primary_key": [], "foreign_keys": []})
                # columns and constraints
                for expression in (stmt.expressions or []):
                    if isinstance(expression, ColumnDef):
                        col_name = self._id_name(expression.name)
                        col_type = (expression.kind.sql(dialect="postgres") if expression.kind else "TEXT").upper()
                        tables[table_name]["columns"][col_name] = col_type
                        # primary key (column-level)
                        if expression.constraints:
                            for c in expression.constraints:
                                if getattr(c, "kind", None) and str(c.kind).upper() == "PRIMARY_KEY":
                                    tables[table_name]["primary_key"].append(col_name)
                    elif isinstance(expression, ForeignKey):
                        # table-level FK
                        cols = [self._id_name(i) for i in (expression.expressions or [])]
                        ref_table = self._table_name(expression.reference)
                        ref_cols = [self._id_name(i) for i in (getattr(expression, "reference_columns", []) or [])]
                        fk = {"table": table_name, "columns": cols, "ref_table": ref_table, "ref_columns": ref_cols}
                        foreign_keys.append(fk)
                        tables[table_name]["foreign_keys"].append({"columns": cols, "ref_table": ref_table, "ref_columns": ref_cols})
                    else:
                        # другие table-level constraints: PRIMARY KEY(...)
                        kind = getattr(expression, "kind", None)
                        if kind and str(kind).upper() == "PRIMARY_KEY":
                            cols = [self._id_name(i) for i in (expression.expressions or [])]
                            tables[table_name]["primary_key"].extend(cols)
        
        return {"tables": tables, "foreign_keys": foreign_keys}
    
    def _table_name(self, node) -> str:
        if isinstance(node, Table):
            return self._id_name(node.this)
        if isinstance(node, Reference):
            return self._id_name(node.this)
        return ""
    
    def _id_name(self, node) -> str:
        if isinstance(node, Identifier):
            return (node.this or "").strip()
        return (str(node) or "").strip().strip('"')
