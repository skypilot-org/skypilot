FROM golang:1.23-alpine AS builder

WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-server ./cmd/fusermount-server/
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-shim ./cmd/fusermount-shim/
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-wrapper ./cmd/fusermount-wrapper/

# sky-fuse-helper runs inside the caller's mount namespace, where this image's
# libraries are not reachable, so link it statically.
RUN apk add --no-cache build-base && \
    gcc -static -O2 -Wall -Wextra -o sky-fuse-helper helper/sky-fuse-helper.c

FROM alpine:3.19

# Copy the fusermount-server binary
COPY --from=builder /app/fusermount-server /usr/local/bin/
COPY --from=builder /app/fusermount-shim /usr/local/bin/
COPY --from=builder /app/fusermount-wrapper /usr/local/bin/
COPY --from=builder /app/sky-fuse-helper /usr/local/bin/

# The server needs to run as root to access namespaces of other containers
USER root
ENTRYPOINT ["/usr/local/bin/fusermount-server"]
