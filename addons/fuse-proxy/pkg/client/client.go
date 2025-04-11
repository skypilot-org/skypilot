package client

import (
	"encoding/json"
	"fmt"
	"net"
	"os"

	mfcputil "github.com/pfnet-research/meta-fuse-csi-plugin/pkg/util"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
)

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

// Fusermount sends a fusermount request to the server
func (c *Client) Fusermount(args []string) (int, error) {
	// Open our mount namespace
	ns, err := os.Open("/proc/self/ns/mnt")
	if err != nil {
		return 0, fmt.Errorf("failed to open mount namespace: %w", err)
	}
	defer ns.Close()

	req := &common.Request{
		Args: args,
	}
	b, err := json.Marshal(req)
	if err != nil {
		return 0, fmt.Errorf("failed to marshal request: %w", err)
	}
	// Send the mnt namespace fd and the request to the server
	if err := mfcputil.SendMsg(c.conn, int(ns.Fd()), b); err != nil {
		return 0, fmt.Errorf("failed to send namespace fd: %w", err)
	}
	fd, jsonBytes, err := mfcputil.RecvMsg(c.conn)
	if err != nil {
		return 0, fmt.Errorf("failed to receive file descriptor: %w", err)
	}
	resp := &common.Response{}
	if err := json.Unmarshal(jsonBytes, &resp); err != nil {
		return 0, fmt.Errorf("failed to unmarshal response: %w", err)
	}
	if !resp.Success {
		return 0, fmt.Errorf("fusermount failed: %s", resp.Error)
	}
	return fd, nil
}

// Close closes the client connection
func (c *Client) Close() error {
	return c.conn.Close()
}
