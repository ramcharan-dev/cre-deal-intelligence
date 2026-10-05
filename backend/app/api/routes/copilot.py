import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import CopilotAnswer, CopilotRequest, SearchHit
from app.db.session import get_db
from app.services.copilot import ask
from app.services.search import search

router = APIRouter(tags=["copilot"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/search",
    response_model=list[SearchHit],
    summary="Search deals, extracted values and emails",
    description="Keyword search. Field and email hits carry the source email and verbatim text.",
)
async def search_all(
    db: DbDep,
    q: Annotated[str, Query(min_length=2, max_length=200, description="Search text")],
    deal_id: Annotated[uuid.UUID | None, Query(description="Limit to one deal")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[SearchHit]:
    return await search(db, q, deal_id, limit)


@router.post(
    "/copilot",
    response_model=CopilotAnswer,
    summary="Ask a question about deals and quotes",
    description=(
        "Common questions (compare quotes, lowest fixed rate, highest proceeds, declined lenders, lenders, "
        "pending actions, summary) are answered from stored data; anything else falls back to search. "
        "Every item lists its source emails. No AI model is called."
    ),
)
async def ask_copilot(body: CopilotRequest, db: DbDep) -> CopilotAnswer:
    return await ask(db, body.question, body.deal_id)
