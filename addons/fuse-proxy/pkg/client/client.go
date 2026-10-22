package client

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"syscall"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	log "k8s.io/klog/v2"
)

// oPath is O_PATH, which pins a file without granting read or write access to
// it. The syscall package does not export it.
const oPath = 0x200000

// Client represents a connection to the fusermount-server
type Client struct {
	conn *net.UnixConn
}

// NewClient creates a new client to the fusermount-server
func NewClient(socketPath string) (*Client, error) {
	conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: socketPath, Net: "unix"})
	if err != nil {
		return nil, fmt.Errorf("failed to connect to server: %w", err)
	}
	return &Client{conn: conn}, nil
}

// Fusermount sends a request with the caller's mount namespace and, for a
// mount, the mountpoint as an O_PATH descriptor. It returns the mounted
// /dev/fuse descriptor for a mount request.
func (c *Client) Fusermount(req *common.Request) (int, error) {
	ns, err := os.Open("/proc/self/ns/mnt")
	if err != nil {
		return 0, fmt.Errorf("failed to open mount namespace: %w", err)
	}
	defer ns.Close()
	fds := []int{int(ns.Fd())}

	if req.Op == common.OpMount && req.Mountpoint != "" {
		// The server resolves the path when it cannot be opened here.
		target, err := openMountpoint(req.Mountpoint)
		if err != nil {
			log.Warningf("Falling back to sending the mountpoint path: %v", err)
		} else {
			defer target.Close()
			fds = append(fds, int(target.Fd()))
			req.HasTargetFD = true
		}
	}

	b, err := json.Marshal(req)
	if err != nil {
		return 0, fmt.Errorf("failed to marshal request: %w", err)
	}
	if err := common.SendFds(c.conn, fds, b); err != nil {
		return 0, fmt.Errorf("failed to send request: %w", err)
	}

	// Kept raw: the descriptor outlives this call.
	recvFds, jsonBytes, err := common.RecvFds(c.conn, 1)
	if err != nil {
		return 0, fmt.Errorf("failed to receive response: %w", err)
	}
	resp := &common.Response{}
	if err := json.Unmarshal(jsonBytes, resp); err != nil {
		common.CloseFds(recvFds)
		return 0, fmt.Errorf("failed to unmarshal response: %w", err)
	}
	if !resp.Success {
		common.CloseFds(recvFds)
		return 0, fmt.Errorf("fusermount failed: %s", resp.Error)
	}
	if req.Op != common.OpMount || len(recvFds) == 0 {
		common.CloseFds(recvFds)
		return 0, nil
	}
	common.CloseFds(recvFds[1:])
	return recvFds[0], nil
}

// openMountpoint opens the mountpoint with the caller's credentials.
func openMountpoint(path string) (*os.File, error) {
	f, err := os.OpenFile(path, oPath|syscall.O_DIRECTORY, 0)
	if err != nil {
		return nil, fmt.Errorf("failed to open mountpoint %s: %w", path, err)
	}
	return f, nil
}

// Close closes the client connection
func (c *Client) Close() error {
	return c.conn.Close()
}
