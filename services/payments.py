import os
import httpx
from fastapi import HTTPException
from database import get_user_by_email

# ─── Live Keys (Primary) ────────────────────────────────────────────────────
SPACEREMIT_PUBLIC_KEY = os.environ.get("SPACEREMIT_LIVE_PUBLIC_KEY")
SPACEREMIT_SECRET_KEY = os.environ.get("SPACEREMIT_LIVE_SECRET_KEY")

# ─── Fallback to Test Keys if Live not set ───────────────────────────────────
if not SPACEREMIT_PUBLIC_KEY:
    SPACEREMIT_PUBLIC_KEY = os.environ.get("SPACEREMIT_TEST_PUBLIC_KEY")
if not SPACEREMIT_SECRET_KEY:
    SPACEREMIT_SECRET_KEY = os.environ.get("SPACEREMIT_TEST_SECRET_KEY")

_is_live = bool(os.environ.get("SPACEREMIT_LIVE_PUBLIC_KEY"))

async def generate_payment_link(email: str, plan_name: str, period: str, amount: float):
    """جلب بيانات الدفع للواجهة الأمامية بأمان"""
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "amount": amount,
        "email": email,
        "public_key": SPACEREMIT_PUBLIC_KEY,
        "is_live": _is_live
    }

async def verify_spaceremit_payment(payment_code: str):
    """التحقق من صحة الدفع عبر الخادم"""
    if not SPACEREMIT_SECRET_KEY:
        print("Error: No SPACEREMIT secret key found in environment variables")
        return None

    url = "https://spaceremit.com/api/v2/payment_info/"
    payload = {
        "private_key": SPACEREMIT_SECRET_KEY,
        "payment_id": payment_code
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload)
            data = response.json()
            print(f"[SpaceRemit Verify] status={data.get('response_status')} code={payment_code}")
            if data.get("response_status") == "success":
                payment_data = data.get("data", {})
                status_tag = payment_data.get("status_tag")
                # A=Completed | T=Test Payment
                if status_tag in ['A', 'T']:
                    return payment_data
                else:
                    print(f"[SpaceRemit Verify] Rejected status_tag={status_tag}")
            else:
                print(f"[SpaceRemit Verify] Failed: {data.get('message')}")
            return None
        except Exception as e:
            print(f"[SpaceRemit API Error]: {e}")
            return None