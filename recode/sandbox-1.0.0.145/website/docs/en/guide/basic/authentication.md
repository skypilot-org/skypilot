# Authentication

Support for JWT authentication using the environment variable `JWT_PUBLIC_KEY` (base64-encoded public key). Below is a complete process from generating keys, starting the service with static key mode, to making successful requests.

## Generate Key Pair

```bash
openssl genrsa -out private_key.pem 2048
openssl rsa -in private_key.pem -pubout -out public_key.pem
echo "Key pair generated!"
```

## Start Service with Public Key for Authentication

```bash
export JWT_PUBLIC_KEY=$(cat public_key.pem | base64)
JWT_PUBLIC_KEY="${JWT_PUBLIC_KEY}"
```

## Generate JWT with Private Key

Business services use the private key to generate a JWT valid for 1 hour (simulating business service issuance):

```bash
# This is a simplified script to generate JWT. In production, business backends should use mature JWT libraries
base64url_encode() { openssl base64 -e -A | tr '+/' '-_' | tr -d '='; }
header='{"alg":"RS256","typ":"JWT"}'
exp_time=$(($(date +%s) + 3600))
payload="{\"exp\":${exp_time}}"
to_be_signed="$(echo -n "$header" | base64url_encode).$(echo -n "$payload" | base64url_encode)"
signature=$(echo -n "$to_be_signed" | openssl dgst -sha256 -sign private_key.pem | base64url_encode)
jwt="${to_be_signed}.${signature}"
echo "JWT generated: ${jwt}"
```

## Access Service with JWT

```bash
curl --silent --show-error -X GET "http://localhost:8080/cdp/json/version" \
     -H "Authorization: Bearer ${jwt}"
```

## Short-lived Ticket Authentication Example (Using VNC as Example)

> For requests that cannot include headers, use `?ticket`

This example demonstrates how to obtain a general ticket and build a URL for VNC service in an authenticated environment.

### Prerequisites

Ensure your service has been started with **static** or **dynamic** mode and authentication configuration is complete.

### Generate Long-term JWT

Generate a long-term valid JWT (simulating a logged-in user):

```bash
# (JWT generation script is the same as previous example)
# ...
jwt="..."
```

### Exchange JWT for Ticket

Use JWT to obtain a one-time ticket from the general endpoint (default validity is 30s, can be configured via `TICKET_TTL_SECONDS` environment variable):

```bash
echo "Exchanging JWT for one-time general ticket..."

ticket_response=$(curl --silent -X POST "http://localhost:8080/tickets" \
     -H "Authorization: Bearer ${jwt}")

ticket=$(echo "$ticket_response" | jq -r .ticket)
expires=$(echo "$ticket_response" | jq -r .expires_in)

echo "Success! Ticket: ${ticket}, Valid for: ${expires} seconds"
```

### Build and Use VNC URL

Now, your frontend application can use the obtained `${ticket}` variable to build the VNC URL and initiate access:

```bash
# Bash script simulating client URL construction
vnc_url="http://localhost:8080/vnc/index.html?ticket=${ticket}&path=websockify%3Fticket%3D${ticket}"

echo "Final URL built by client: ${vnc_url}"

# Simulate access (should be done in browser)
# curl -I "${vnc_url}"
```