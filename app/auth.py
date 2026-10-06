from typing import Optional

from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer, BadSignature
import os
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(os.getenv("APP_SECRET","dev-secret-change-me"), salt="leadpilot-session")

def hash_password(password:str)->str: return pwd.hash(password)
def verify_password(password:str, hashed:str)->bool: return pwd.verify(password, hashed)
def make_session(user_id:int)->str: return serializer.dumps({"uid":user_id})
def read_session(token: Optional[str]):
    if not token: return None
    try: return serializer.loads(token).get("uid")
    except BadSignature: return None
