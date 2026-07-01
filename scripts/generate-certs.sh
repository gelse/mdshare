#!/bin/bash
# Generates self-signed SSL certificates for mdshare development/CI
# Usage: ./scripts/generate-certs.sh [output_dir]

OUT_DIR="${1:-./certs}"
mkdir -p "$OUT_DIR"

# Generate self-signed cert valid for 365 days
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$OUT_DIR/privkey.pem" \
  -out "$OUT_DIR/fullchain.pem" \
  -subj "/C=DE/ST=State/L=City/O=mdshare/CN=localhost"

# Set restrictive permissions on private key
chmod 600 "$OUT_DIR/privkey.pem"

echo "Certificates generated in $OUT_DIR/"
ls -la "$OUT_DIR/"
