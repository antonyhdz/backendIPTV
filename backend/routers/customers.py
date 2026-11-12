import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from backend.dependencies import database_dependency, require_admin
from backend.routers.management_helpers import paginated, serialize_customer, valid_object_id
from backend.schemas import CustomerCreate, CustomerUpdate

router = APIRouter(prefix="/api/v1/admin/customers", tags=["Clientes"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_customer(data: CustomerCreate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    document = data.model_dump()
    document.update(is_active=True, created_at=now, updated_at=now)
    try:
        result = database.customers.insert_one(document)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email already belongs to a customer.") from exc
    document["_id"] = result.inserted_id
    return serialize_customer(document)


@router.get("")
def list_customers(page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), search: str | None = None, active: bool | None = None, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    filters = {}
    if active is not None:
        filters["is_active"] = active
    if search and search.strip():
        escaped = re.escape(search.strip()[:100])
        filters["$or"] = [{field: {"$regex": escaped, "$options": "i"}} for field in ("name", "email", "phone")]
    result = paginated(database.customers, filters, page, page_size, [("created_at", DESCENDING)])
    result["items"] = [serialize_customer(item) for item in result["items"]]
    return result


@router.patch("/{customer_id}")
def update_customer(customer_id: str, data: CustomerUpdate, database: Database = Depends(database_dependency), _: dict = Depends(require_admin)):
    object_id = valid_object_id(customer_id)
    if object_id is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    updates["updated_at"] = datetime.now(timezone.utc)
    try:
        document = database.customers.find_one_and_update({"_id": object_id}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email already belongs to a customer.") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return serialize_customer(document)
