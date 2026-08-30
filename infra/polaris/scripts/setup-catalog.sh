#!/bin/sh
set -eu

# Local runtime-validation bootstrap for Polaris 1.7.x.
# The API flow follows the official Polaris quickstart, but keeps the setup
# idempotent so `docker compose up` can be run repeatedly during learning.

apk add --no-cache jq >/dev/null

REALM="${POLARIS_REALM:-POLARIS}"
CLIENT_ID="${POLARIS_CLIENT_ID:-root}"
CLIENT_SECRET="${POLARIS_CLIENT_SECRET:-s3cr3t}"
CATALOG_NAME="${POLARIS_CATALOG_NAME:-commerce_catalog}"
BUCKET="${WAREHOUSE_BUCKET:-commerce-lakehouse}"
REGION="${S3_REGION:-us-west-2}"
POLARIS_BASE="http://polaris:8181"

printf '%s\n' "Obtaining Polaris root token..."
TOKEN="$(curl --fail-with-body -sS \
  "$POLARIS_BASE/api/catalog/v1/oauth/tokens" \
  --user "$CLIENT_ID:$CLIENT_SECRET" \
  -H "Polaris-Realm: $REALM" \
  -d grant_type=client_credentials \
  -d scope=PRINCIPAL_ROLE:ALL | jq -r '.access_token')"

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "Failed to obtain Polaris token" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer $TOKEN"
REALM_HEADER="Polaris-Realm: $REALM"

# GET first so repeated local starts do not fail on AlreadyExists.
HTTP_CODE="$(curl -sS -o /tmp/catalog.json -w '%{http_code}' \
  -H "$AUTH_HEADER" \
  -H "$REALM_HEADER" \
  "$POLARIS_BASE/api/management/v1/catalogs/$CATALOG_NAME")"

if [ "$HTTP_CODE" = "200" ]; then
  echo "Polaris catalog already exists: $CATALOG_NAME"
elif [ "$HTTP_CODE" = "404" ]; then
  echo "Creating Polaris catalog: $CATALOG_NAME"
  PAYLOAD="$(cat <<EOF
{
  "catalog": {
    "name": "$CATALOG_NAME",
    "type": "INTERNAL",
    "readOnly": false,
    "properties": {
      "default-base-location": "s3://$BUCKET"
    },
    "storageConfigInfo": {
      "storageType": "S3",
      "allowedLocations": ["s3://$BUCKET"],
      "endpoint": "http://rustfs:9000",
      "endpointInternal": "http://rustfs:9000",
      "pathStyleAccess": true,
      "region": "$REGION"
    }
  }
}
EOF
)"

  curl --fail-with-body -sS \
    -X POST "$POLARIS_BASE/api/management/v1/catalogs" \
    -H "$AUTH_HEADER" \
    -H "$REALM_HEADER" \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD" >/dev/null
else
  echo "Unexpected response while reading catalog: HTTP $HTTP_CODE" >&2
  cat /tmp/catalog.json >&2 || true
  exit 1
fi

# The default catalog_admin role exists for an INTERNAL catalog. Granting
# CATALOG_MANAGE_CONTENT makes the local root principal usable by Spark for
# create/read/write validation, matching the official RustFS guide pattern.
echo "Ensuring CATALOG_MANAGE_CONTENT on catalog_admin..."
GRANT_CODE="$(curl -sS -o /tmp/grant.json -w '%{http_code}' \
  -X PUT "$POLARIS_BASE/api/management/v1/catalogs/$CATALOG_NAME/catalog-roles/catalog_admin/grants" \
  -H "$AUTH_HEADER" \
  -H "$REALM_HEADER" \
  -H 'Content-Type: application/json' \
  -d '{"type":"catalog","privilege":"CATALOG_MANAGE_CONTENT"}')"

case "$GRANT_CODE" in
  200|201|204)
    echo "Catalog privilege granted."
    ;;
  409)
    echo "Catalog privilege already granted."
    ;;
  *)
    echo "Failed to grant CATALOG_MANAGE_CONTENT: HTTP $GRANT_CODE" >&2
    cat /tmp/grant.json >&2 || true
    exit 1
    ;;
esac

echo "Polaris catalog setup complete: $CATALOG_NAME -> s3://$BUCKET"
