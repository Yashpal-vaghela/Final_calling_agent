from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.services.city_service import (
    get_covered_cities,
    fetch_city_from_postal_api_async,
    validate_city_pincode_match_async,
)

router = APIRouter()


class CityPincodeValidationRequest(BaseModel):
    city: str = Field(..., min_length=1)
    pincode: str = Field(..., min_length=6, max_length=6)


@router.get("/api/covered-cities")
async def get_cities():
    """
    Returns the list of covered cities for frontend form datalists.
    """
    cities = get_covered_cities()
    return JSONResponse(content=cities, status_code=200)


@router.get("/api/pincode-lookup/{pincode}")
async def lookup_pincode(pincode: str):
    """
    Looks up the city directly from the Postal PIN code API (api.postalpincode.in).
    """
    clean = pincode.strip()
    city = await fetch_city_from_postal_api_async(clean)
    if city:
        return JSONResponse(
            content={"success": True, "city": city, "pincode": clean},
            status_code=200
        )
    return JSONResponse(
        content={
            "success": False,
            "error": f"City details could not be identified for PIN code '{clean}' via Postal API."
        },
        status_code=404
    )


@router.post("/api/validate-city-pincode")
async def validate_city_pincode(req: CityPincodeValidationRequest):
    """
    Validates consistency between user-selected city and area PIN code using the Postal API.
    """
    is_valid, err_msg = await validate_city_pincode_match_async(req.city, req.pincode)
    return JSONResponse(
        content={
            "valid": is_valid,
            "message": err_msg or "Valid match between city and PIN code."
        },
        status_code=200
    )



