package server

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strings"
	"syscall"

	mfcputil "github.com/pfnet-research/meta-fuse-csi-plugin/pkg/util"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	log "k8s.io/klog/v2"
)

const (
	// FUSE device major and minor numbers
	FuseMajor = 10
	FuseMinor = 229
)

// Server represents the fusermount-server
type Server struct {
	fusermountPath string
	listener       net.Listener
	socketPath     string
}

// NewServer creates a new server instance
func NewServer(fusermountPath, socketPath string) *Server {
	return &Server{
		fusermountPath: fusermountPath,
		socketPath:     socketPath,
	}
}

// Start begins listening for and handling requests
func (s *Server) Start() error {
	// Remove existing socket file if it exists
	if err := os.Remove(s.socketPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to remove existing socket: %w", err)
	}
	// Create Unix Domain Socket listener
	listener, err := net.Listen("unix", s.socketPath)
	if err != nil {
		return fmt.Errorf("failed to listen on socket: %w", err)
	}
	s.listener = listener
	// Set socket permissions to allow all users to connect
	if err := os.Chmod(s.socketPath, 0o666); err != nil {
		return fmt.Errorf("failed to set socket permissions: %w", err)
	}
	log.Infof("Server listening on unix socket: %s", s.socketPath)
	// Accept and handle connections
	for {
		conn, err := listener.Accept()
		if err != nil {
			if strings.Contains(err.Error(), "use of closed network connection") {
				return nil
			}
			log.Errorf("Failed to accept connection: %v", err)
			continue
		}
		go s.handleConnection(conn)
	}
}

// Stop stops the server
func (s *Server) Stop() error {
	if s.listener != nil {
		return s.listener.Close()
	}
	return nil
}

func (s *Server) handleConnection(conn net.Conn) {
	defer conn.Close()
	// Convert net.Conn to *net.UnixConn for fd passing
	unixConn, ok := conn.(*net.UnixConn)
	if !ok {
		log.Error("Connection is not a Unix domain socket connection")
		return
	}
	req := &common.Request{}
	// Receive the namespace fd and fusermount request
	nsFd, msg, err := mfcputil.RecvMsg(unixConn)
	if err != nil {
		log.Errorf("Failed to receive namespace fd: %v", err)
		return
	}
	if err := json.Unmarshal(msg, req); err != nil {
		log.Errorf("Failed to unmarshal request: %v", err)
		return
	}
	log.Infof("Received ns fd %d, request: %+v, start mount", nsFd, req)
	// Do fusermount and return mounted fd for mount request
	fd, err := s.handleFusermount(req, nsFd)
	resp := &common.Response{
		Success: err == nil,
	}
	if err != nil {
		resp.Error = err.Error()
	}
	b, err := json.Marshal(resp)
	if err != nil {
		log.Errorf("Failed to marshal response: %v", err)
	}
	if err := mfcputil.SendMsg(unixConn, fd, b); err != nil {
		log.Errorf("Failed to send response: %v", err)
		return
	}
}

func (s *Server) handleFusermount(req *common.Request, nsFd int) (int, error) {
	nsPath := fmt.Sprintf("/proc/self/fd/%d", nsFd)
	// Ensure /dev/fuse exists in the container's mnt namespace
	if err := s.ensureFuseDevice(nsPath); err != nil {
		return 0, fmt.Errorf("failed to ensure /dev/fuse exists: %w", err)
	}
	// Create socket pair for FD passing
	socks, err := createSocketPair()
	if err != nil {
		return 0, fmt.Errorf("failed to create socket pair: %w", err)
	}
	defer socks[0].Close()
	defer socks[1].Close()

	// Get the underlying file for the second socket
	sock1File, err := socks[1].(*net.UnixConn).File()
	if err != nil {
		return 0, fmt.Errorf("failed to get socket file: %w", err)
	}
	defer sock1File.Close()
	// Prepare nsenter command to run fusermount in container's namespace
	nsenterArgs := []string{
		"--mount=" + nsPath,
		s.fusermountPath,
	}
	nsenterArgs = append(nsenterArgs, req.Args...)

	cmd := exec.Command("nsenter", nsenterArgs...)

	// Pass the socket FD to fusermount
	cmd.ExtraFiles = []*os.File{sock1File}
	cmd.Env = append(os.Environ(),
		fmt.Sprintf("%s=3", common.EnvFuseCommFD), // FD 3 is the first extra file
	)
	// Capture command output for error reporting
	output, err := cmd.CombinedOutput()
	if err != nil {
		return 0, fmt.Errorf("fusermount failed: %w, output: %s", err, string(output))
	}
	// For mount operations, we need to transfer the fd back to the caller
	isUmount := isUmount(req.Args)
	if !isUmount {
		fd, _, err := mfcputil.RecvMsg(socks[0])
		if err != nil {
			return 0, fmt.Errorf("failed to receive FD: %w", err)
		}
		return fd, nil
	}
	return 0, nil
}

// ensureFuseDevice ensures that /dev/fuse exists in the container's namespace
func (s *Server) ensureFuseDevice(nspath string) error {
	// First check if /dev/fuse exists in the container's namespace
	checkCmd := exec.Command("nsenter", "--mount="+nspath, "stat", "/dev/fuse")
	if err := checkCmd.Run(); err == nil {
		// Device exists, no need to create
		return nil
	}

	// Device doesn't exist, create it
	// Note: We need to create the device with mknod in the container's namespace
	createCmd := exec.Command("nsenter",
		"--mount="+nspath,
		"mknod",
		"/dev/fuse",
		"c",
		fmt.Sprintf("%d", FuseMajor),
		fmt.Sprintf("%d", FuseMinor),
	)

	if output, err := createCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("failed to create /dev/fuse: %w, output: %s", err, string(output))
	}

	// Set permissions to allow user access
	chmodCmd := exec.Command("nsenter",
		"--mount="+nspath,
		"chmod",
		"666",
		"/dev/fuse",
	)

	if output, err := chmodCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("failed to set permissions on /dev/fuse: %w, output: %s", err, string(output))
	}

	return nil
}

// createSocketPair creates a pair of connected Unix domain sockets
func createSocketPair() ([2]net.Conn, error) {
	// Create a pair of socket file descriptors
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		return [2]net.Conn{}, fmt.Errorf("socketpair failed: %w", err)
	}
	// Convert the file descriptors to net.Conn
	conn1, err1 := net.FileConn(os.NewFile(uintptr(fds[0]), ""))
	if err1 != nil {
		return [2]net.Conn{}, fmt.Errorf("failed to create first connection: %w", err1)
	}
	conn2, err2 := net.FileConn(os.NewFile(uintptr(fds[1]), ""))
	if err2 != nil {
		conn1.Close()
		return [2]net.Conn{}, fmt.Errorf("failed to create second connection: %w", err2)
	}
	return [2]net.Conn{conn1, conn2}, nil
}
