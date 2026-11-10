import asyncio
import time

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from backend.app_database import SessionLocal, get_db
from backend.app_models import AuthSession, User, utc_now
from backend.app_schemas import LoginInput, LogoutInput, RefreshInput, TokenResponse
from backend.app_security import decode_access_token
from backend.app_services.auth_service import (
    ActiveDeviceSessionError,
    authenticate_user,
    build_login_response,
    refresh_client_access,
    revoke_client_session,
)
from backend.app_services.session_events import session_event_hub


router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Autenticación"],
)


@router.websocket("/events")
async def client_session_events(websocket: WebSocket):
    """Push revocation events to a connected client app."""
    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except asyncio.TimeoutError:
        await websocket.close(code=4408, reason="Authentication timed out")
        return
    except WebSocketDisconnect:
        return

    if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
        await websocket.close(code=4401, reason="Send an auth message first")
        return

    db = SessionLocal()
    session_id = None
    try:
        try:
            payload = decode_access_token(str(auth_message.get("access_token", "")))
            user_id = int(payload["sub"])
            session_id = int(payload["sid"])
        except (jwt.InvalidTokenError, ValueError, KeyError, TypeError):
            await websocket.close(code=4401, reason="Invalid access token")
            return

        user = db.get(User, user_id)
        client_session = db.get(AuthSession, session_id)
        if (
            user is None or not user.is_active or user.role != "customer"
            or client_session is None or client_session.user_id != user_id
            or client_session.revoked_at is not None or client_session.expires_at <= utc_now()
        ):
            await websocket.close(code=4401, reason="Client session is not active")
            return

        await session_event_hub.connect(user_id, session_id, websocket)
        # Recheck after registration so a concurrent revocation cannot be missed.
        db.expire_all()
        client_session = db.get(AuthSession, session_id)
        user = db.get(User, user_id)
        if (
            client_session is None or client_session.revoked_at is not None
            or client_session.expires_at <= utc_now() or user is None or not user.is_active
        ):
            await websocket.send_json({"type": "session_revoked", "reason": "session_inactive"})
            await websocket.close(code=4403, reason="Client session revoked")
            return

        await websocket.send_json({"type": "ready"})
        next_check = time.monotonic() + 20
        while True:
            try:
                timeout = max(0.1, next_check - time.monotonic())
                message = await asyncio.wait_for(websocket.receive_json(), timeout=timeout)
            except asyncio.TimeoutError:
                message = None
            if isinstance(message, dict) and message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            if time.monotonic() >= next_check:
                db.expire_all()
                client_session = db.get(AuthSession, session_id)
                user = db.get(User, user_id)
                if (
                    client_session is None or client_session.revoked_at is not None
                    or client_session.expires_at <= utc_now() or user is None or not user.is_active
                ):
                    await websocket.send_json({"type": "session_revoked", "reason": "session_inactive"})
                    await websocket.close(code=4403, reason="Client session revoked")
                    return
                await websocket.send_json({"type": "ping"})
                next_check = time.monotonic() + 20
    except WebSocketDisconnect:
        pass
    finally:
        if session_id is not None:
            session_event_hub.disconnect(session_id, websocket)
        db.close()


@router.post("/login", response_model=TokenResponse)
def login(
    data: LoginInput,
    db: Session = Depends(get_db),
):
    """Autentica al usuario y devuelve un token de acceso."""

    user = authenticate_user(
        db=db,
        username=data.username,
        password=data.password,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.role == "customer" and not data.device_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La app debe enviar el identificador aleatorio de este dispositivo.",
        )
    try:
        return build_login_response(user, db, data.device_id)
    except ActiveDeviceSessionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    data: RefreshInput,
    db: Session = Depends(get_db),
):
    """Rota el token persistente del dispositivo de un cliente."""
    result = refresh_client_access(db, data.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión del cliente venció o fue revocada. Inicia sesión de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return result


@router.post("/logout")
async def logout(
    data: LogoutInput,
    db: Session = Depends(get_db),
):
    """Revoca la sesión de ese dispositivo."""
    revoked = revoke_client_session(db, data.refresh_token)
    if revoked:
        await session_event_hub.notify_user(revoked[0], "logout")
    return {"detail": "Sesión cerrada."}


