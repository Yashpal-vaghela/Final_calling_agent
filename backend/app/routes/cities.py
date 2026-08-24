from fastapi import APIRouter
from fastapi.responses import JSONResponse

from backend.app.services.city_service import get_covered_cities

router = APIRouter()

@router.get("/api/covered-cities")
async def get_cities():
    """
    Returns the list of covered cities for the frontend forms.
    """
    cities = get_covered_cities()
    return JSONResponse(content=cities, status_code=200)
