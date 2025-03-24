package common

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
)

const (
	EnvFuseCommFD = "_FUSE_COMMFD"
	EnvSharedDir  = "FUSERMOUNT_SHARED_DIR"

	ShimBinPath = "/usr/local/bin/fusermount-shim"
)

// MustGetServerSocketPath returns the path to the server socket for client-server communication, panic if not found
func MustGetServerSocketPath() string {
	return filepath.Join(getSharedDir(), "server.sock")
}

// MustGetFuseCommFD returns the file descriptor set by the caller of fusermount, panic if not found or illegal
func MustGetFuseCommFD() int {
	fdStr := os.Getenv(EnvFuseCommFD)
	if fdStr == "" {
		panic(fmt.Sprintf("Environment variable %s is not set", EnvFuseCommFD))
	}
	fd, err := strconv.Atoi(fdStr)
	if err != nil {
		panic(fmt.Sprintf("Illegal value %s for %s environment variable", fdStr, EnvFuseCommFD))
	}
	return fd
}

// MustGetShimInstallPath returns the path to install the fusermount shim, panic if not found
func MustGetShimInstallPath() string {
	return filepath.Join(getSharedDir(), "fusermount-shim")
}

func getSharedDir() string {
	if os.Getenv(EnvSharedDir) == "" {
		panic("FUSERMOUNT_SHARED_DIR environment variable must be set")
	}
	return os.Getenv(EnvSharedDir)
}

// Request represents a fusermount request
type Request struct {
	Args []string `json:"args"`
}

// Response represents a fusermount response
type Response struct {
	Success bool   `json:"success"`
	Error   string `json:"error,omitempty"`
}
