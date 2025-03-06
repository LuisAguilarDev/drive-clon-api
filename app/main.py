from fastapi import FastAPI, Depends
from pydantic import BaseModel
from typing import List, Annotated
from app.db.database import Base, engine, SessionLocal
from sqlalchemy.orm import Session
from app.routes import auth
from app.routes.auth import get_current_user
import app.models as models
app = FastAPI()
app.include_router(auth.router)
Base.metadata.create_all(bind=engine)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict,Depends(get_current_user)]
# Body
class File(BaseModel):
    #required
    id: str
    #not required
    public: bool = False

@app.get("/")
def root():
    return {"Hello":"World"}

# query params
@app.get("/file")
def file(file:str):
    # if not item:
    #     raise HTTPException(status_code=400, detail="Item should be provided")

    return {"item":file}

# query params
@app.get("/fileId/{file_id}")
def file(file_id:str):
    # if not item:
    #     raise HTTPException(status_code=400, detail="Item should be provided")
    return {"item2":file_id}

# query params
@app.get("/files_locked",response_model=list[File])
def file(user:user_dependency,limit: int = 10):
    print("user")
    # if not item:
    #     raise HTTPException(status_code=400, detail="Item should be provided")
    return [{"id":"123","public":False},{"id":"124","public":False}][0:limit]

@app.post("/files")
async def create_file(file:File,db:db_dependency):
    db_file = models.Files(address=file.id)
    db.add(db_file)
    db.commit()
    db.refresh(db_file)