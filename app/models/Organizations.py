from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


class Organizations(Base):
    """Espejo local de una organización de Keycloak.

    Keycloak es la fuente de verdad de la membresía; esta tabla guarda el
    vínculo para que el backend resuelva el tenant (`org_id`) sin depender de
    que el claim ya esté presente en el token.
    """

    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    keycloak_org_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    # Soft delete: una fila está "viva" cuando deleted_at IS NULL.
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)
