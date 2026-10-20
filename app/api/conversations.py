import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import api_key_fingerprint
from app.api.deps import get_db_session
from app.config import Settings, get_settings
from app.db import repositories
from app.schemas.conversations import (
    ConversationDetail,
    ConversationSummary,
    RenameConversationRequest,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ConversationSummary]:
    """List this scope's conversations, most recently active first."""
    return repositories.list_conversations(
        session, api_key_hash=api_key_fingerprint(settings)
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ConversationDetail:
    """Fetch one conversation with its full message list."""
    detail = repositories.get_conversation(
        session, str(conversation_id), api_key_hash=api_key_fingerprint(settings)
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: uuid.UUID,
    payload: RenameConversationRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ConversationSummary:
    """Rename a conversation; 404 when absent or out of scope."""
    conversation_id_str = str(conversation_id)
    if renamed := repositories.rename_conversation(
        session,
        conversation_id_str,
        payload.title,
        api_key_hash=api_key_fingerprint(settings),
    ):
        return repositories.get_conversation_summary(
            session, conversation_id_str, api_key_hash=api_key_fingerprint(settings)
        )
    else:
        raise HTTPException(status_code=404, detail="Conversation not found.")


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Delete a conversation and its messages; 404 when absent or out of scope."""
    if deleted := repositories.delete_conversation(
        session,
        str(conversation_id),
        api_key_hash=api_key_fingerprint(settings),
    ):
        return {"deleted": "true", "conversation_id": str(conversation_id)}
    else:
        raise HTTPException(status_code=404, detail="Conversation not found.")
