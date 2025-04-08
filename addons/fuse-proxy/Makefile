.PHONY: vet fmt tidy reviewable gofumpt

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

gofumpt: GOFUMPT_BIN
	$(GOFUMPT) -l -w .

GOFUMPT = $(shell pwd)/bin/gofumpt
GOFUMPT_BIN:
	$(call go-get-tool,$(GOFUMPT),mvdan.cc/gofumpt@latest)

# go-get-tool will 'go get' any package $2 and install it to $1.
PROJECT_DIR := $(shell dirname $(abspath $(lastword $(MAKEFILE_LIST))))
define go-get-tool
@[ -f $(1) ] || { \
set -e ;\
echo "Downloading $(2)" ;\
GOBIN=$(PROJECT_DIR)/bin go install $(2); \
}
endef