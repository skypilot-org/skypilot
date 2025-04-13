FROM golang:1.23-alpine AS builder

WORKDIR /app
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-server ./cmd/fusermount-server/
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-shim ./cmd/fusermount-shim/
RUN CGO_ENABLED=0 GOOS=linux go build -o fusermount-wrapper ./cmd/fusermount-wrapper/

FROM alpine:3.19

# Install nsenter and fuse
RUN apk add --no-cache util-linux fuse

# Copy the fusermount-server binary
COPY --from=builder /app/fusermount-server /usr/local/bin/
COPY --from=builder /app/fusermount-shim /usr/local/bin/
COPY --from=builder /app/fusermount-wrapper /usr/local/bin/

# The server needs to run as root to access namespaces of other containers
USER root
ENTRYPOINT ["/usr/local/bin/fusermount-server"]
