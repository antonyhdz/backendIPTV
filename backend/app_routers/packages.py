
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app_database import get_db
from backend.app_dependencies import require_admin
from backend.app_models import Channel, Package, PackageChannel, User
from backend.app_schemas import PackageCreate, PackageUpdate


router = APIRouter(
    prefix="/api/v1/admin/packages",
    tags=["Paquetes"],
)


def package_response(package: Package, db: Session) -> dict:
    assignments = db.scalars(
        select(PackageChannel).where(PackageChannel.package_id == package.id)
    ).all()

    channel_ids = [assignment.channel_id for assignment in assignments]

    channels = (
        db.scalars(
            select(Channel).where(Channel.id.in_(channel_ids))
        ).all()
        if channel_ids else []
    )

    return {
        "id": package.id,
        "name": package.name,
        "duration_days": package.duration_days,
        "price_cents": package.price_cents,
        "active": package.active,
        "channel_ids": channel_ids,
        "channels": [
            {"id": channel.id, "name": channel.name}
            for channel in channels
        ],
        "created_at": (
            package.created_at.isoformat()
            if package.created_at is not None
            else None
        ),
    }


@router.get("")
def list_packages(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    packages = db.scalars(
        select(Package).order_by(Package.id)
    ).all()

    return {
        "packages": [package_response(package, db) for package in packages]
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_package(
    data: PackageCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    name = data.name.strip()

    existing = db.scalar(
        select(Package).where(Package.name == name)
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Ya existe un paquete con ese nombre.",
        )

    channels = db.scalars(
        select(Channel).where(Channel.id.in_(data.channel_ids))
    ).all()

    if len({channel.id for channel in channels}) != len(set(data.channel_ids)):
        raise HTTPException(
            status_code=404,
            detail="Uno o más canales seleccionados no existen.",
        )

    package = Package(
        name=name,
        duration_days=data.duration_days,
        price_cents=data.price_cents,
        active=data.active,
    )

    db.add(package)
    db.flush()

    db.add_all([
        PackageChannel(
            package_id=package.id,
            channel_id=channel.id
        )
        for channel in channels
    ])

    try:
        db.commit()
        db.refresh(package)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="No se pudo crear el paquete porque el nombre ya existe.",
        )

    return package_response(package, db)


@router.patch("/{package_id}")
def update_package_channels(
    package_id: int,
    data: PackageUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    package = db.get(Package, package_id)

    if package is None:
        raise HTTPException(
            status_code=404,
            detail="Paquete no encontrado.",
        )

    channel_ids = set(data.channel_ids)

    if not channel_ids:
        raise HTTPException(
            status_code=422,
            detail="Selecciona al menos un canal para el paquete.",
        )

    channels = db.scalars(
        select(Channel).where(Channel.id.in_(channel_ids))
    ).all()

    if len(channels) != len(channel_ids):
        raise HTTPException(
            status_code=404,
            detail="Uno o más canales seleccionados no existen.",
        )

    db.query(PackageChannel).filter(
        PackageChannel.package_id == package.id
    ).delete(synchronize_session=False)

    db.add_all([
        PackageChannel(
            package_id=package.id,
            channel_id=channel.id
        )
        for channel in channels
    ])

    db.commit()
    db.refresh(package)

    return package_response(package, db)
