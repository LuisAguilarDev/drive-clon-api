#!/bin/sh
# ---------------------------------------------------------------------------
# Configuración inicial de Keycloak para Drive Clon (POC).
#
# Crea, de forma IDEMPOTENTE (se puede re-ejecutar sin duplicar):
#   1. El realm `driveclon`.
#   2. El client público `driveclon-ui` (SPA, Authorization Code + PKCE).
#   3. Google como Identity Provider federado (si hay credenciales en el env).
#
# Pensado como job one-shot en docker-compose, igual que `minio-init`. Como
# `start-dev` usa H2 en memoria, este job vuelve a aplicar la config en cada
# recreación del contenedor de Keycloak.
#
# Los secretos de Google llegan por variables de entorno (GOOGLE_CLIENT_ID /
# GOOGLE_CLIENT_SECRET) desde el `.env` (no commiteado). Nunca se hardcodean.
# ---------------------------------------------------------------------------
set -u

KCADM=/opt/keycloak/bin/kcadm.sh
SERVER="${KC_SERVER:-http://keycloak:8080}"
KC_ADMIN="${KC_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-admin}"
REALM="${KC_REALM:-driveclon}"
CLIENT_ID="${KC_CLIENT_ID:-driveclon-ui}"
APP_ORIGIN="${APP_ORIGIN:-http://localhost:5173}"
# Client confidencial del backend (service account) para la Admin API.
BACKEND_CLIENT_ID="${KC_BACKEND_CLIENT_ID:-driveclon-backend}"
BACKEND_CLIENT_SECRET="${KC_BACKEND_CLIENT_SECRET:-driveclon-backend-secret}"

echo "[keycloak-init] esperando a Keycloak en $SERVER ..."
until $KCADM config credentials --server "$SERVER" --realm master \
      --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD" >/dev/null 2>&1; do
  sleep 3
done
echo "[keycloak-init] Keycloak listo, admin autenticado."

# --- Realm -----------------------------------------------------------------
# `organizationsEnabled=true` activa la feature Organizations EN ESTE realm. El
# flag global `--features=organization` solo la hace disponible; sin esto, la
# Admin API de organizaciones responde 404.
if $KCADM get "realms/$REALM" >/dev/null 2>&1; then
  echo "[keycloak-init] realm '$REALM' ya existe; asegurando organizationsEnabled."
  $KCADM update "realms/$REALM" -s organizationsEnabled=true
else
  echo "[keycloak-init] creando realm '$REALM'."
  $KCADM create realms -s realm="$REALM" -s enabled=true -s organizationsEnabled=true
fi

# --- Client público (SPA, Authorization Code + PKCE) -----------------------
EXISTING_CLIENT=$($KCADM get clients -r "$REALM" -q clientId="$CLIENT_ID" 2>/dev/null)
case "$EXISTING_CLIENT" in
  *"\"$CLIENT_ID\""*)
    echo "[keycloak-init] client '$CLIENT_ID' ya existe."
    ;;
  *)
    echo "[keycloak-init] creando client '$CLIENT_ID'."
    $KCADM create clients -r "$REALM" \
      -s clientId="$CLIENT_ID" \
      -s enabled=true \
      -s publicClient=true \
      -s standardFlowEnabled=true \
      -s directAccessGrantsEnabled=false \
      -s "redirectUris=[\"$APP_ORIGIN/*\"]" \
      -s "webOrigins=[\"$APP_ORIGIN\"]" \
      -s 'attributes."pkce.code.challenge.method"=S256' \
      -s 'attributes."post.logout.redirect.uris"=+'
    ;;
esac

# --- Client confidencial del backend (service account, Admin API) ----------
# El backend usa este client (client_credentials) para crear organizaciones y
# añadir miembros vía la Admin API. En Keycloak 26 las organizaciones son un
# recurso a nivel de realm, así que la permisología la da `manage-realm` (NO
# existe un rol `manage-organizations`); `manage-users` permite añadir miembros.
EXISTING_BACKEND=$($KCADM get clients -r "$REALM" -q clientId="$BACKEND_CLIENT_ID" 2>/dev/null)
case "$EXISTING_BACKEND" in
  *"\"$BACKEND_CLIENT_ID\""*)
    echo "[keycloak-init] client '$BACKEND_CLIENT_ID' ya existe."
    ;;
  *)
    echo "[keycloak-init] creando client '$BACKEND_CLIENT_ID'."
    $KCADM create clients -r "$REALM" \
      -s clientId="$BACKEND_CLIENT_ID" \
      -s enabled=true \
      -s publicClient=false \
      -s secret="$BACKEND_CLIENT_SECRET" \
      -s standardFlowEnabled=false \
      -s directAccessGrantsEnabled=false \
      -s serviceAccountsEnabled=true
    ;;
esac

# Asignar (idempotente) los roles necesarios al service account del backend.
SA_USER="service-account-$BACKEND_CLIENT_ID"
for ROLE in manage-realm view-realm manage-users view-users; do
  echo "[keycloak-init] asignando rol '$ROLE' a '$SA_USER'."
  $KCADM add-roles -r "$REALM" \
    --uusername "$SA_USER" \
    --cclientid realm-management \
    --rolename "$ROLE" >/dev/null 2>&1 || \
    echo "[keycloak-init]   (rol '$ROLE' ya asignado o no disponible)"
done

# --- Google como Identity Provider federado --------------------------------
if [ -n "${GOOGLE_CLIENT_ID:-}" ] && [ -n "${GOOGLE_CLIENT_SECRET:-}" ]; then
  if $KCADM get "identity-provider/instances/google" -r "$REALM" >/dev/null 2>&1; then
    echo "[keycloak-init] IdP 'google' ya existe; actualizando credenciales."
    $KCADM update "identity-provider/instances/google" -r "$REALM" \
      -s trustEmail=true \
      -s "config.clientId=$GOOGLE_CLIENT_ID" \
      -s "config.clientSecret=$GOOGLE_CLIENT_SECRET"
  else
    echo "[keycloak-init] creando IdP 'google'."
    # trustEmail=true: Google ya verifica el email, así que Keycloak crea al
    # usuario con emailVerified=true en vez de pedir verificación de nuevo.
    $KCADM create identity-provider/instances -r "$REALM" \
      -s alias=google \
      -s providerId=google \
      -s enabled=true \
      -s trustEmail=true \
      -s "config.clientId=$GOOGLE_CLIENT_ID" \
      -s "config.clientSecret=$GOOGLE_CLIENT_SECRET" \
      -s "config.defaultScope=openid profile email" \
      -s config.syncMode=IMPORT
  fi

  # IdP mapper: importa el claim `picture` de Google a un atributo de usuario.
  # syncMode=FORCE → lo actualiza en cada login (no sólo el primero).
  if $KCADM get "identity-provider/instances/google/mappers" -r "$REALM" 2>/dev/null | grep -q '"name" : "picture-importer"'; then
    echo "[keycloak-init] IdP mapper 'picture-importer' ya existe."
  else
    echo "[keycloak-init] creando IdP mapper 'picture-importer'."
    $KCADM create "identity-provider/instances/google/mappers" -r "$REALM" \
      -s name=picture-importer \
      -s identityProviderAlias=google \
      -s identityProviderMapper=oidc-user-attribute-idp-mapper \
      -s 'config."syncMode"=FORCE' \
      -s 'config."claim"=picture' \
      -s 'config."user.attribute"=picture'
  fi

  # Protocol mapper en el client SPA: expone el atributo `picture` como claim en
  # el token (access + id + userinfo) para que el backend y el frontend lo lean.
  UI_CLIENT_UUID=$($KCADM get clients -r "$REALM" -q clientId="$CLIENT_ID" --fields id --format csv --noquotes 2>/dev/null)
  if $KCADM get "clients/$UI_CLIENT_UUID/protocol-mappers/models" -r "$REALM" 2>/dev/null | grep -q '"name" : "picture"'; then
    echo "[keycloak-init] protocol mapper 'picture' ya existe."
  else
    echo "[keycloak-init] creando protocol mapper 'picture' en '$CLIENT_ID'."
    $KCADM create "clients/$UI_CLIENT_UUID/protocol-mappers/models" -r "$REALM" \
      -s name=picture \
      -s protocol=openid-connect \
      -s protocolMapper=oidc-usermodel-attribute-mapper \
      -s 'config."user.attribute"=picture' \
      -s 'config."claim.name"=picture' \
      -s 'config."jsonType.label"=String' \
      -s 'config."id.token.claim"=true' \
      -s 'config."access.token.claim"=true' \
      -s 'config."userinfo.token.claim"=true'
  fi
else
  echo "[keycloak-init] GOOGLE_CLIENT_ID/SECRET no definidos; omito el IdP de Google."
fi

echo "[keycloak-init] configuración completa."
