.PHONY: vet fmt tidy reviewable gofumpt build-deps

# Default go command
GO ?= go

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)

reviewable: fmt vet tidy gofumpt
	git diff --exit-code || (echo "Changes detected after static checks. Run 'cd addons/fuse-proxy && make reviewable' and commit the changes." && exit 1)

fmt:
	$(GO) fmt ./...

vet:
	$(GO) vet ./...

# Tidy and verify Go dependencies
tidy:
	$(GO) mod tidy
	$(GO) mod verify

gofumpt:
	gofumpt -l -w .

build-deps:
	$(GO) install mvdan.cc/gofumpt@latest

