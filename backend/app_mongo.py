import operator
import logging
from datetime import datetime, timezone
from typing import Any, Type

from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, UnaryExpression
from sqlalchemy.sql.operators import in_op, not_in_op, is_, is_not

from backend.app_config import settings

_logger = logging.getLogger(__name__)

_mongo_client: MongoClient | None = None
_mongo_db = None


def get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None and settings.USE_MONGO:
        try:
            _mongo_client = MongoClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=8000,
                connectTimeoutMS=8000,
            )
            _mongo_db = _mongo_client[settings.MONGODB_DB_NAME]
            # Test connection
            _mongo_db.command("ping")
            _logger.info("Conectado exitosamente a MongoDB Atlas: %s", settings.MONGODB_DB_NAME)
        except Exception as exc:
            _logger.error("Error conectando a MongoDB Atlas: %s", exc)
            raise
    return _mongo_db


def get_next_sequence(collection_name: str) -> int:
    db = get_mongo_db()
    if db is None:
        raise RuntimeError("MongoDB no está disponible")
    counter = db.counters.find_one_and_update(
        {"_id": collection_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return counter["seq"]


def init_mongo_indexes():
    db = get_mongo_db()
    if db is None:
        return
    try:
        db.users.create_index("username", unique=True)
        db.users.create_index("id", unique=True)
        db.channels.create_index("id", unique=True)
        db.channels.create_index("category")
        db.packages.create_index("id", unique=True)
        db.packages.create_index("name", unique=True)
        db.package_channels.create_index([("package_id", 1), ("channel_id", 1)], unique=True)
        db.channel_favorites.create_index([("user_id", 1), ("channel_id", 1)], unique=True)
        db.channel_views.create_index([("user_id", 1), ("channel_id", 1)])
        db.customer_messages.create_index([("customer_id", 1), ("created_at", -1)])
        db.auth_sessions.create_index("refresh_token_hash", unique=True)
        db.auth_sessions.create_index("user_id")
        db.subscriptions.create_index("customer_id")
        db.subscriptions.create_index("id", unique=True)
        _logger.info("Índices de MongoDB inicializados correctamente.")
    except Exception as exc:
        _logger.warning("Aviso al crear índices en MongoDB: %s", exc)


def _clean_val(val):
    if hasattr(val, "value"):
        return _clean_val(val.value)
    name = type(val).__name__
    if name == "True_":
        return True
    if name == "False_":
        return False
    if name == "Null":
        return None
    return val


def _parse_where_criteria(clause) -> dict:
    if clause is None:
        return {}
    if isinstance(clause, BooleanClauseList):
        result = {}
        for c in clause.clauses:
            part = _parse_where_criteria(c)
            for k, v in part.items():
                if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                    result[k].update(v)
                else:
                    result[k] = v
        return result
    if isinstance(clause, BinaryExpression):
        left_col = getattr(clause.left, "name", str(clause.left))
        val = _clean_val(clause.right)
        op = clause.operator
        if op == operator.eq:
            return {left_col: val}
        elif op == operator.ne:
            return {left_col: {"$ne": val}}
        elif op in (operator.gt, getattr(operator, "__gt__", None)):
            return {left_col: {"$gt": val}}
        elif op in (operator.ge, getattr(operator, "__ge__", None)):
            return {left_col: {"$gte": val}}
        elif op in (operator.lt, getattr(operator, "__lt__", None)):
            return {left_col: {"$lt": val}}
        elif op in (operator.le, getattr(operator, "__le__", None)):
            return {left_col: {"$lte": val}}
        elif op == in_op:
            val_list = list(val) if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)) else [val]
            return {left_col: {"$in": val_list}}
        elif op == not_in_op:
            val_list = list(val) if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)) else [val]
            return {left_col: {"$nin": val_list}}
        elif op == is_:
            return {left_col: val}
        elif op == is_not:
            return {left_col: {"$ne": val}}
    return {}


class MongoResult:
    def __init__(self, items: list):
        self._items = items

    def all(self) -> list:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def __iter__(self):
        return iter(self._items)


class MongoSession:
    """
    Sesión compatible con SQLAlchemy que ejecuta consultas y persistencia en MongoDB Atlas.
    """

    def __init__(self, db=None):
        self.db = db if db is not None else get_mongo_db()
        self._pending_add = []
        self._pending_delete = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    def add(self, entity):
        if entity not in self._pending_add:
            self._pending_add.append(entity)

    def add_all(self, entities):
        for entity in entities:
            self.add(entity)

    def delete(self, entity):
        if entity not in self._pending_delete:
            self._pending_delete.append(entity)

    def flush(self):
        for entity in self._pending_add:
            table_name = getattr(entity, "__tablename__", None)
            if not table_name:
                continue

            # Auto-asignar ID si es None
            if getattr(entity, "id", None) is None:
                entity.id = get_next_sequence(table_name)

            data = self._entity_to_dict(entity)
            self.db[table_name].replace_one({"id": entity.id}, data, upsert=True)

        self._pending_add.clear()

    def commit(self):
        self.flush()

        for entity in self._pending_delete:
            table_name = getattr(entity, "__tablename__", None)
            if not table_name:
                continue
            entity_id = getattr(entity, "id", None)
            if entity_id is not None:
                self.db[table_name].delete_one({"id": entity_id})
            else:
                # Caso clave compuesta (como package_channels o channel_favorites)
                data = self._entity_to_dict(entity)
                self.db[table_name].delete_one(data)

        self._pending_add.clear()
        self._pending_delete.clear()

    def rollback(self):
        self._pending_add.clear()
        self._pending_delete.clear()

    def refresh(self, entity):
        table_name = getattr(entity, "__tablename__", None)
        entity_id = getattr(entity, "id", None)
        if table_name and entity_id is not None:
            doc = self.db[table_name].find_one({"id": entity_id})
            if doc:
                self._apply_dict_to_entity(entity, doc)

    def get(self, entity_class: Type, ident: Any):
        table_name = getattr(entity_class, "__tablename__", None)
        if not table_name:
            return None
        doc = self.db[table_name].find_one({"id": ident})
        if not doc:
            return None
        return self._dict_to_entity(entity_class, doc)

    def scalar(self, statement) -> Any:
        info = self._compile_stmt(statement)
        table_name = info["table"]
        if not table_name:
            return None

        collection = self.db[table_name]

        if info["is_count"]:
            return collection.count_documents(info["filter"])

        cursor = collection.find(info["filter"])
        if info["sort"]:
            cursor = cursor.sort(info["sort"])
        doc = next(cursor, None)
        if not doc:
            return None

        if info["projected_column"]:
            return doc.get(info["projected_column"])

        if info["model"]:
            return self._dict_to_entity(info["model"], doc)

        return doc

    def scalars(self, statement) -> MongoResult:
        info = self._compile_stmt(statement)
        table_name = info["table"]
        if not table_name:
            return MongoResult([])

        collection = self.db[table_name]

        # Manejo especial para JOINs conocidos
        if info.get("joins"):
            filter_query = dict(info["filter"])
            for join_table in info["joins"]:
                if join_table == "channel_favorites" and "user_id" in filter_query:
                    uid = filter_query.pop("user_id")
                    fav_ids = [d["channel_id"] for d in self.db.channel_favorites.find({"user_id": uid})]
                    filter_query["id"] = {"$in": fav_ids}
                elif join_table == "package_channels" and "package_id" in filter_query:
                    pid = filter_query.pop("package_id")
                    ch_ids = [d["channel_id"] for d in self.db.package_channels.find({"package_id": pid})]
                    filter_query["id"] = {"$in": ch_ids}
            cursor = collection.find(filter_query)
        else:
            cursor = collection.find(info["filter"])

        if info["sort"]:
            cursor = cursor.sort(info["sort"])
        if info["offset"]:
            cursor = cursor.skip(info["offset"])
        if info["limit"]:
            cursor = cursor.limit(info["limit"])

        items = []
        for doc in cursor:
            if info["projected_column"]:
                items.append(doc.get(info["projected_column"]))
            elif info["model"]:
                items.append(self._dict_to_entity(info["model"], doc))
            else:
                items.append(doc)

        return MongoResult(items)

    def execute(self, statement) -> MongoResult:
        # Maneja casos específicos como popular_rows con joins
        info = self._compile_stmt(statement)
        if info.get("joins") and "channel_views" in info.get("joins"):
            filter_query = dict(info["filter"])
            uid = filter_query.get("user_id")
            views = list(self.db.channel_views.find({"user_id": uid}) if uid else self.db.channel_views.find())
            channel_ids = [v["channel_id"] for v in views]
            channels = {c["id"]: self._dict_to_entity(info["model"], c)
                        for c in self.db.channels.find({"id": {"$in": channel_ids}})}
            rows = []
            for v in views:
                ch = channels.get(v["channel_id"])
                if ch:
                    rows.append((ch, v.get("id")))
            return MongoResult(rows)

        # Fallback a scalars
        res = self.scalars(statement)
        return MongoResult([(item,) for item in res.all()])

    def close(self):
        pass

    def _compile_stmt(self, stmt) -> dict:
        descs = getattr(stmt, "column_descriptions", [])
        model_cls = None
        is_count = False
        projected_column = None

        for d in descs:
            if d.get("entity") is not None:
                model_cls = d["entity"]
                break
            expr = d.get("expr")
            if hasattr(expr, "name") and expr.name.lower() == "count":
                is_count = True
            elif hasattr(expr, "table") and hasattr(expr, "name"):
                projected_column = expr.name
                table_name = getattr(expr.table, "name", None)
                if table_name:
                    from backend import app_models
                    for attr in dir(app_models):
                        cls = getattr(app_models, attr)
                        if isinstance(cls, type) and getattr(cls, "__tablename__", None) == table_name:
                            model_cls = cls
                            break

        if model_cls is None:
            for f in stmt.get_final_froms():
                if hasattr(f, "entity"):
                    model_cls = f.entity
                    break
                elif hasattr(f, "element") and hasattr(f.element, "entity"):
                    model_cls = f.element.entity
                    break
                elif hasattr(f, "name"):
                    table_name = f.name
                    from backend import app_models
                    for attr in dir(app_models):
                        cls = getattr(app_models, attr)
                        if isinstance(cls, type) and getattr(cls, "__tablename__", None) == table_name:
                            model_cls = cls
                            break

        table_name = getattr(model_cls, "__tablename__", None)
        if not table_name and descs:
            expr = descs[0].get("expr")
            if hasattr(expr, "table") and hasattr(expr.table, "name"):
                table_name = expr.table.name

        filter_dict = _parse_where_criteria(stmt.whereclause)

        sort_list = []
        for o in getattr(stmt, "_order_by_clauses", []):
            target = getattr(o, "element", o)
            col_name = getattr(target, "name", None)
            modifier = getattr(o, "modifier", None)
            direction = -1 if str(modifier).lower() == "desc" else 1
            if col_name:
                sort_list.append((col_name, direction))

        joins = []
        for j in getattr(stmt, "_setup_joins", []):
            join_target = j[0]
            if hasattr(join_target, "__tablename__"):
                joins.append(join_target.__tablename__)
            elif hasattr(join_target, "name"):
                joins.append(join_target.name)

        return {
            "table": table_name,
            "model": model_cls,
            "filter": filter_dict,
            "sort": sort_list,
            "is_count": is_count,
            "projected_column": projected_column,
            "limit": getattr(stmt, "_limit", None),
            "offset": getattr(stmt, "_offset", None),
            "joins": joins,
        }

    def _entity_to_dict(self, entity) -> dict:
        data = {}
        for col in getattr(entity, "__table__", []).columns if hasattr(entity, "__table__") else []:
            col_name = col.name
            if hasattr(entity, col_name):
                data[col_name] = getattr(entity, col_name)
        if not data:
            # Fallback para dataclasses o clases comunes
            for k, v in entity.__dict__.items():
                if not k.startswith("_"):
                    data[k] = v
        return data

    def _dict_to_entity(self, model_cls: Type, doc: dict):
        clean_doc = dict(doc)
        clean_doc.pop("_id", None)
        entity = model_cls(**clean_doc)
        return entity

    def _apply_dict_to_entity(self, entity, doc: dict):
        clean_doc = dict(doc)
        clean_doc.pop("_id", None)
        for k, v in clean_doc.items():
            setattr(entity, k, v)

