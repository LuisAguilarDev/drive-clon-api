from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.db.database import Base


class Users(Base):
    """Espejo local del usuario de Keycloak.

    La identidad y las credenciales las gestiona Keycloak; aquí sólo guardamos
    el `sub` (identificador estable del token) y a qué organización pertenece,
    para filtrar por tenant.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    keycloak_sub = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, default="")
    picture = Column(String, default="")
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    # Soft delete: una fila está "viva" cuando deleted_at IS NULL.
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
