from sqlalchemy.orm import Session
from app.models.Users import Users
from sqlalchemy.future import select

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    async def create_user(self, email: str, hashed_password: str):
        new_user = Users(
            email=email, password=hashed_password, provider="email")
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    async def create_user_firebase(self, email: str, name: str, picture: str):
        new_user = Users(email=email, password="",
                         provider="google", name=name, picture=picture)
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    async def create_user_firebase(self, email: str, name: str, picture: str):
        new_user = Users(email=email, password="",
                         provider="google", name=name, picture=picture)
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    # async def findUser(self, email: str):
    #     stmt = select(Users).where(Users.email == email)
    #     # async with self.db.begin():  # 👈 Asegúrate de usar async with
    #     result = await self.db.execute(stmt)
    #     user = result.scalars().first()  # Obtener el primer resultado o None
    #     return user
