from pymongo import MongoClient

from backend.config import settings

_client: MongoClient | None = None


def connect_database():
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=8000,
            connectTimeoutMS=8000,
            tz_aware=True,
        )
    database = _client[settings.mongodb_db_name]
    database.command("ping")
    return database


def get_database():
    if _client is None:
        return connect_database()
    return _client[settings.mongodb_db_name]


def close_database() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
