import os
import logging
from pydantic import BaseModel, ValidationError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from passlib.context import CryptContext
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
API_URL = os.getenv('GEMINI_API_URL')

# Password Hashing Configuration
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

# CORS Configuration
origins = ["https://your-allowed-origin.com"]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Pydantic model for input validation
class InputData(BaseModel):
    param1: str
    param2: int

# Retry logic for Gemini API
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def call_gemini_api(param1: str, param2: int):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{API_URL}?param1={param1}&param2={param2}")
        response.raise_for_status()  # Raise an error for bad responses
        return response.json()

# Endpoint example
@app.post("/api/gemini")
async def gemini_endpoint(data: InputData):
    try:
        result = await call_gemini_api(data.param1, data.param2)
        return result
    except HTTPException as e:
        logger.error(f"HTTP Exception: {e}")
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
