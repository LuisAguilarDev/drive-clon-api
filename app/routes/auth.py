from datetime import timedelta, datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette import status
from app.db.database import SessionLocal
from app.models.Users import Users
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from jose import jwt, JWTError
from app.repositories.user_repository import UserRepository
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.core.config import settings
from firebase_admin import auth
from fastapi.responses import JSONResponse


router = APIRouter(
    prefix='/auth',
    tags=['auth']
)


bcrypt_context = CryptContext(schemes=['bcrypt'],deprecated='auto')
oauth2_bearer = OAuth2PasswordBearer(tokenUrl='auth/token')

class CreateUserRequest(BaseModel):
    email: str
    password:str


class CreateUserRequestFirebase(BaseModel):
    email: str
    password: str
    name: str
    picture: str

class Token(BaseModel):
    access_token: str
    token_type:str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]

@router.post("/",status_code=status.HTTP_201_CREATED)
async def create_user(create_user_request:CreateUserRequest,db:db_dependency):
    user_repo = UserRepository(db)
    print(create_user_request)
    hashed_password=bcrypt_context.hash(create_user_request.password)
    try:
        await user_repo.create_user(
            email=create_user_request.email,
            hashed_password=hashed_password
        )
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already exists."
        )
    
    except SQLAlchemyError as e:
        # Error genérico de SQLAlchemy
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred."
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)  # Incluye el mensaje original del error
        )


@router.post("/login",response_model=Token)
async def login_for_access_token(form_data:Annotated[OAuth2PasswordRequestForm,Depends()],db:db_dependency):
    user = authenticate_user(form_data.username,form_data.password,db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='Could not validate user.')
    token = create_access_token(
        user.email, user.id, timedelta(minutes=60 * 24))
    return {'access_token':token,'token_type': 'bearer'}



def authenticate_user(email:str,password:str,db):
    user = db.query(Users).filter(Users.email == email).first()
    if not user:
        return False
    if not bcrypt_context.verify(password,user.password):
        return False
    return user


def create_access_token(username: str, user_id: int, picture: str, name: str, expires_delta: timedelta):
    encode = {'sub': username, 'id': user_id, "picture": picture, "name": name}
    expires = datetime.utcnow() + expires_delta
    encode.update({'exp':expires})
    return jwt.encode(encode,settings.SECRET_KEY,algorithm=settings.ALGORITHM)

def get_current_user(token:Annotated[str,Depends(oauth2_bearer)]):
    try:
        payload = jwt.decode(token,settings.SECRET_KEY,algorithms=[settings.ALGORITHM])
        email: str = payload.get('sub')
        user_id:int = payload.get('id')
        if email is None or user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='Could not validate the user.')
        return {'email':email,'id':user_id}

    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='Could not validate the user.')


# Ruta para validar el ID Token de Firebase y devolver un JWT personalizado
@router.get("/validate")
async def validate_firebase_token(request: Request, response: Response, db: db_dependency):
    user_repo = UserRepository(db)
    try:
        # Obtener el token desde las cookies
        token = request.cookies.get("token")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail='Token cookie is missing.'
            )

        # Verificar el ID Token de Firebase
        decoded_token = auth.verify_id_token(token)

        # si el usuario no esta creado lo crea
        # usercreated = await user_repo.findUser(decoded_token.get("email"))
        # print("usercreated:", usercreated)

        token = create_access_token(
            decoded_token.get("email"), decoded_token.get("uid"), decoded_token.get("picture"), decoded_token.get("name"), timedelta(minutes=60 * 24))
        # Configurar la cookie segura y HttpOnly para el JWT
        response = JSONResponse(
            content={"message": "Authenticated successfully!"})
        response.set_cookie(
            key="token",       # Nombre de la cookie para el JWT
            value=token,        # Valor (el JWT)
            httponly=True,          # Evita acceso desde JavaScript
            secure=True,            # Solo en HTTPS
            samesite="strict",      # Evita CSRF
            max_age=60 * 60 * 24 * 1  # Expira en 1 días
        )

        return response

    except Exception as e:
        print("Error:", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase ID token"
        )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("token", path="/")
    return {"message": "Logged out successfully"}


async def create_user_from_firebase(create_user_request: CreateUserRequest, db: db_dependency):
    user_repo = UserRepository(db)
    hashed_password = bcrypt_context.hash(create_user_request.password)
    try:
        await user_repo.create_user(
            email=create_user_request.email,
            hashed_password=hashed_password
        )
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already exists."
        )

    except SQLAlchemyError as e:
        # Error genérico de SQLAlchemy
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred."
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)  # Incluye el mensaje original del error
        )
