import httpx
import asyncio
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
        agent_number: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        self.bearer_token = bearer_token or settings.SMARTFLO_BEARER_TOKEN
        self.base_url = (base_url or getattr(settings, "SMARTFLO_BASE_URL", "") or "https://api-smartflo.tatateleservices.com").rstrip("/")
        self.caller_id = caller_id or settings.SMARTFLO_CALLER_ID
        self.agent_number = agent_number or settings.SMARTFLO_AGENT_NUMBER
        self.api_key = api_key or settings.SMARTFLO_API_KEY

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
        if "click_to_call_support" in self.base_url:
            url = self.base_url
        else:
            url = f"{self.base_url}/v1/click_to_call_support"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.bearer_token}"
        }

        target_caller_id = caller_id or self.caller_id
        target_agent = agent_number or self.agent_number
        
        payload: Dict[str, Any] = {
            "customer_number": customer_number,
            "agent_number": target_agent,
            "caller_id": target_caller_id,
            "async": 1
        }

        if self.api_key:
            payload["api_key"] = self.api_key

        if custom_params:
            payload.update(custom_params)

        logger.info(f"[SmartfloClient] Triggering Click-to-Call for customer: {customer_number} via {url}")

        def _do_request():
            import requests
            res = requests.post(url, json=payload, headers=headers, timeout=15)
            try:
                return res.json()
            except Exception:
                return {"status_code": res.status_code, "text": res.text}

        try:
            data = await asyncio.to_thread(_do_request)
            logger.info(f"[SmartfloClient] Click-to-Call result: {data}")
            return data
        except Exception as e:
            logger.error(f"[SmartfloClient] Exception initiating Click-to-Call: {e}")
            return {
                "success": False,
                "error": str(e)
            }

smartflo_client = SmartfloClient()

