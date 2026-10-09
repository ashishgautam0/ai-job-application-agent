from fastapi import APIRouter, Query

from ..models.schemas import CompanyResearchRequest
from company_research import research_company
from tracker import get_cached_research, save_research_cache

router = APIRouter()


@router.get("/cached")
def cached_intel(name: str = Query(...)):
    """Company intel from the cache only (written by the hourly routine).

    Returns {"found": false} instead of 404 when nothing fresh is cached —
    the job page treats that as "not researched yet".
    """
    row = get_cached_research(name)
    if not row:
        return {"found": False}
    return {"found": True, **row}


@router.post("")
def research(
    body: CompanyResearchRequest,
):
    cached = get_cached_research(body.company_name)
    if cached:
        return cached

    result = research_company(body.company_name)
    if result.get("product_url") or result.get("hiring_contact", {}).get("name"):
        save_research_cache(body.company_name, result)

    return result