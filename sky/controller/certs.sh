#!/bin/bash
# https://github.com/camelop/rust-mtls-example
# TODO(suquark): generate files under '~/.sky/certs'?

mkdir -p certificates
cd certificates

## Server CA
# Generate a key for the CA
openssl genrsa -out ca.key 2048
# Generate a self signed certificate for the CA
openssl req -new -x509 -key ca.key -days 7300 -subj "/CN=SkyPilot CA" -out ca.crt

## Server
# Generate an RSA key for the domain (localhost here)
openssl genrsa -out server.key 2048
# optional: inspect the key
openssl rsa -in server.key -noout -text
# optional: extract pubkey
openssl rsa -in server.key -pubout -out server.pubkey
# Generate a Certificate Signing Request (CSR)
# enter detailed information when necessary (please make sure you enter COMMON NAME)
openssl req -new -key server.key -subj "/CN=localhost" -addext "subjectAltName = DNS:localhost" -out server.csr
# optional: inspect the csr (note: while inspecting, make sure your Signature Algorithm is not MD5 which is not accepted by many sites, upgrade your openssl if necessary)
openssl req -in server.csr -noout -text
# Sign the domain certificate
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -extfile <(printf "subjectAltName=DNS:localhost") -out server.crt
# optional: to exam the output crt
openssl x509 -in server.crt -noout -text
# Create another file that contains the domain certificate and the ca certificate
cat server.crt ca.crt > server.bundle.crt

## Client CA
# Generate a key for the CA
openssl genrsa -out ca.client.key 2048
# Generate a self signed certificate for the CA
openssl req -new -x509 -key ca.client.key -days 7300 -subj "/CN=SkyPilot CA" -out ca.client.crt

## Client
# Generate an RSA key for the client
openssl genrsa -out client_0.key 2048
# Generate a Certificate Signing Request (CSR), please note that the SAN extension is NECESSARY
# enter detailed information when necessary (please make sure you enter COMMON NAME)
openssl req -new -key client_0.key -subj "/CN=Client" -addext "subjectAltName = DNS:localhost" -out client_0.csr
# Use CA key to sign it
openssl x509 -req -in client_0.csr -CA ca.client.crt -CAkey ca.client.key -CAcreateserial -extfile <(printf "subjectAltName=DNS:localhost") -out client_0.crt
# generate pem file
cat client_0.crt client_0.key > client_0.pem
