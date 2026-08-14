import httpx
import logging
from typing import Optional, Dict, Any
from backend.app.settings import settings

logger = logging.getLogger(__name__)

class SmartfloClient:
    """
    Client for interacting with Tata Smartflo REST APIs,
    specifically the Click-to-Call Support service.
    """
    def __init__(
        self,
        bearer_token: Optional[str] = None,
        base_url: Optional[str] = None,
        caller_id: Optional[str] = None,
        agent_number: Optional[str] = None
    ):
        self.bearer_token = bearer_token or settings.SMARTFLO_BEARER_TOKEN
        self.base_url = (base_url or settings.SMARTFLO_BASE_URL).rstrip("/")
        self.caller_id = caller_id or settings.SMARTFLO_CALLER_ID
        self.agent_number = agent_number or settings.SMARTFLO_AGENT_NUMBER

    async def initiate_click_to_call(
        self,
        customer_number: str,
        agent_number: Optional[str] = None,
        caller_id: Optional[str] = None,
        custom_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Initiates an outbound Click-to-Call Support call via Tata Smartflo API.
        Smartflo calls the customer first, and upon answer, connects to your AI Voice Bot.

        Doc: https://docs.smartflo.tatatelebusiness.com/reference/v1click_to_call_support
        """
        url = f"{self.base_url}/v1/click_to_call_support"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.bearer_token}"
        }

        payload: Dict[str, Any] = {
            "agent_number": agent_number or self.agent_number,
            "customer_number": customer_number,
            "caller_id": caller_id or self.caller_id
        }

        if custom_params:
            payload.update(custom_params)

        logger.info(f"[SmartfloClient] Triggering Click-to-Call for customer: {customer_number}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                logger.info(f"[SmartfloClient] Click-to-Call successful: {data}")
                return data
            except httpx.HTTPStatusError as e:
                logger.error(f"[SmartfloClient] HTTP Error initiating Click-to-Call: {e.response.status_code} - {e.response.text}")
                return {
                    "success": False,
                    "error": f"HTTP {e.response.status_code}",
                    "details": e.response.text
                }
            except Exception as e:
                logger.error(f"[SmartfloClient] Exception initiating Click-to-Call: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }

smartflo_client = SmartfloClient()
