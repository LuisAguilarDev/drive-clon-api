from sqlalchemy.orm import Session
from app.models.Users import Users

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    async def create_user(self, email: str, hashed_password: str):
        new_user = Users(email=email, password=hashed_password)
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user