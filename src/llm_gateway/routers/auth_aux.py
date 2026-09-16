from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from llm_gateway.auth.manager import get_user_manager, UserManager

router = APIRouter()

class VerifyTokenRequest(BaseModel):
    token: str

@router.post("/auth/verify-forgot-password-token")
async def verify_forgot_password_token(
    request: VerifyTokenRequest,
    user_manager: UserManager = Depends(get_user_manager)
) -> dict:
    user = await user_manager.verify_reset_token(request.token)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"status": "valid", "email": user.email}
