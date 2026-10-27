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


@router.get("/api/dentists-by-city/{city}")
async def get_dentists_by_city(city: str):
    """
    Returns authorized smile designers (dentists) for the requested city.
    Uses cached live USD API. Never throws 404/500 if city has no dentists.
    """
    from backend.app.services.dentist_service import get_dentist_service, CITY_ALIASES
    from backend.app.services.city_service import get_nearest_covered_city

    clean_city = city.strip()
    if not clean_city:
        return JSONResponse(
            content={
                "success": True,
                "city": "",
                "dentists": [],
                "nearest_center": None,
                "count": 0
            },
            status_code=200
        )

    # Check city aliases (e.g. delhi -> New Delhi, gurgaon -> Gurugram)
    canonical_lookup = CITY_ALIASES.get(clean_city.lower(), clean_city)

    dentist_service = get_dentist_service()
    directory = await dentist_service.get_directory_async()

    matched_dentists = []
    for dir_city, dentists in directory.items():
        if dir_city.lower() == clean_city.lower() or dir_city.lower() == canonical_lookup.lower():
            matched_dentists = dentists
            break

    nearest_center = None
    if not matched_dentists:
        nearest_center = get_nearest_covered_city(clean_city)

    return JSONResponse(
        content={
            "success": True,
            "city": clean_city,
            "dentists": matched_dentists,
            "nearest_center": nearest_center,
            "count": len(matched_dentists)
        },
        status_code=200
    )



