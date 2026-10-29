import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.auth import AuthActor, get_actor, scope_kwargs
from app.api.deps import get_db_session
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
    actor: AuthActor = Depends(get_actor),
) -> list[ConversationSummary]:
    """List this actor's conversations, most recently active first."""
    return repositories.list_conversations(session, **scope_kwargs(actor))


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> ConversationDetail:
    """Fetch one conversation with its full message list."""
    detail = repositories.get_conversation(
        session, str(conversation_id), **scope_kwargs(actor)
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return detail


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: uuid.UUID,
    payload: RenameConversationRequest,
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> ConversationSummary:
    """Rename a conversation; 404 when absent or out of scope."""
    conversation_id_str = str(conversation_id)
    if renamed := repositories.rename_conversation(
        session,
        conversation_id_str,
        payload.title,
        **scope_kwargs(actor),
    ):
        return repositories.get_conversation_summary(
            session, conversation_id_str, **scope_kwargs(actor)
        )
    else:
        raise HTTPException(status_code=404, detail="Conversation not found.")


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    actor: AuthActor = Depends(get_actor),
) -> dict[str, str]:
    """Delete a conversation and its messages; 404 when absent or out of scope."""
    if deleted := repositories.delete_conversation(
        session,
        str(conversation_id),
        **scope_kwargs(actor),
    ):
        return {"deleted": "true", "conversation_id": str(conversation_id)}
    else:
        raise HTTPException(status_code=404, detail="Conversation not found.")
