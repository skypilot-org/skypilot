package server

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	log "k8s.io/klog/v2"
)

// Server represents the fusermount-server
type Server struct {
	helperPath string
	listener   net.Listener
	socketPath string

	// nsLocks serialises requests per target mount namespace.
	nsLocks sync.Map
}

// request is a fusermount request in either wire format.
type request struct {
	unmount     bool
	lazy        bool
	mountpoint  string
	options     []string
	hasTargetFD bool
}

// NewServer creates a new server instance
func NewServer(helperPath, socketPath string) *Server {
	return &Server{
		helperPath: helperPath,
		socketPath: socketPath,
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

	// The mount namespace, then optionally the mountpoint.
	fds, msg, err := common.RecvFds(unixConn, 2)
	if err != nil {
		log.Errorf("Failed to receive request: %v", err)
		return
	}
	files := common.WrapFds(fds)
	// A held namespace descriptor blocks ephemeral storage cleanup.
	defer common.CloseFiles(files)

	req := &common.Request{}
	if err := json.Unmarshal(msg, req); err != nil {
		log.Errorf("Failed to unmarshal request: %v", err)
		return
	}
	log.Infof("Received %d fd(s), handling request: %+v", len(files), req)

	fuseFile, err := s.handle(req, files)
	resp := &common.Response{Success: err == nil}
	if err != nil {
		log.Errorf("Request failed: %v", err)
		resp.Error = common.TruncateError(err.Error())
	}
	b, marshalErr := json.Marshal(resp)
	if marshalErr != nil {
		log.Errorf("Failed to marshal response: %v", marshalErr)
		return
	}

	var sendFds []int
	if fuseFile != nil {
		// Close the /dev/fuse fd after SendFds hands it to the client
		// via SCM_RIGHTS. If we keep ours open, the kernel's
		// fuse_dev_release never sees dev_count drop to 0 when rclone
		// dies, so fuse_conn_kill is skipped and in-flight FUSE
		// requests stay wedged in request_wait_answer -- making the pod
		// un-killable.
		defer func() {
			if err := fuseFile.Close(); err != nil {
				log.Errorf("Failed to close fuse fd: %v", err)
			} else {
				log.Info("Closed fuse fd")
			}
		}()
		sendFds = append(sendFds, int(fuseFile.Fd()))
	}
	if err := common.SendFds(unixConn, sendFds, b); err != nil {
		log.Errorf("Failed to send response: %v", err)
	}
}

// handle performs one request and, for a mount, returns the /dev/fuse
// descriptor to hand back to the caller.
func (s *Server) handle(req *common.Request, files []*os.File) (*os.File, error) {
	if len(files) == 0 {
		return nil, fmt.Errorf("request carried no mount namespace fd")
	}
	nsFile := files[0]

	parsed, err := resolveRequest(req)
	if err != nil {
		return nil, err
	}
	if parsed.mountpoint == "" && !parsed.hasTargetFD {
		return nil, fmt.Errorf("request has no mountpoint")
	}

	var targetFile *os.File
	if parsed.hasTargetFD {
		if len(files) < 2 {
			return nil, fmt.Errorf("request claims a mountpoint fd but carried none")
		}
		targetFile = files[1]
	}

	unlock := s.lockNamespace(nsFile)
	defer unlock()

	if parsed.unmount {
		if parsed.mountpoint == "" {
			return nil, fmt.Errorf("unmount request has no mountpoint")
		}
		return nil, s.runUnmount(nsFile, parsed)
	}
	return s.runMount(nsFile, targetFile, parsed)
}

func (s *Server) runMount(nsFile, targetFile *os.File, req *request) (*os.File, error) {
	params, err := BuildMountParams(req.options)
	if err != nil {
		return nil, err
	}

	fuseFile, err := openFuseDev()
	if err != nil {
		return nil, err
	}

	// ExtraFiles start at descriptor 3 in the helper.
	extra := []*os.File{nsFile, fuseFile}
	// user_id and group_id 0 match the ownership of existing mounts.
	args := []string{
		"mount",
		"--ns-fd=3",
		"--fuse-fd=4",
		"--flags=" + strconv.FormatUint(uint64(params.Flags), 10),
		"--opts=" + params.KernelOpts,
		"--type=" + params.Type,
		"--source=" + params.Source,
		"--user-id=0",
		"--group-id=0",
		"--ensure-dev",
	}
	if targetFile != nil {
		extra = append(extra, targetFile)
		args = append(args, "--target-fd=5")
	} else {
		args = append(args, "--target="+req.mountpoint)
	}

	if err := s.runHelper(args, extra); err != nil {
		fuseFile.Close()
		return nil, err
	}
	return fuseFile, nil
}

func (s *Server) runUnmount(nsFile *os.File, req *request) error {
	// By path: stat-ing a descriptor blocks on a FUSE mount whose daemon died.
	args := []string{
		"umount",
		"--ns-fd=3",
		"--target=" + req.mountpoint,
	}
	if req.lazy {
		args = append(args, "--lazy")
	}
	return s.runHelper(args, []*os.File{nsFile})
}

// runHelper executes the privileged helper.
func (s *Server) runHelper(args []string, extra []*os.File) error {
	cmd := exec.Command(s.helperPath, args...)
	cmd.ExtraFiles = extra
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %w", strings.TrimSpace(string(output)), err)
	}
	if len(output) > 0 {
		log.Infof("helper output: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// resolveRequest prefers the structured fields and parses Args otherwise.
func resolveRequest(req *common.Request) (*request, error) {
	switch req.Op {
	case common.OpMount, common.OpUmount:
		return &request{
			unmount:     req.Op == common.OpUmount,
			lazy:        req.Lazy,
			mountpoint:  req.Mountpoint,
			options:     req.Options,
			hasTargetFD: req.HasTargetFD,
		}, nil
	case "":
		parsed, err := common.ParseFusermountArgs(req.Args)
		if err != nil {
			return nil, fmt.Errorf("failed to parse fusermount arguments: %w", err)
		}
		return &request{
			unmount:    parsed.Unmount,
			lazy:       parsed.Lazy,
			mountpoint: parsed.Mountpoint,
			options:    parsed.Options,
		}, nil
	default:
		return nil, fmt.Errorf("unknown operation %q", req.Op)
	}
}

// openFuseDev opens /dev/fuse in blocking mode: the Go runtime would otherwise
// try to drive it with the poller, which /dev/fuse does not support.
func openFuseDev() (*os.File, error) {
	fd, err := syscall.Open("/dev/fuse", syscall.O_RDWR|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("failed to open /dev/fuse: %w", err)
	}
	return os.NewFile(uintptr(fd), "/dev/fuse"), nil
}

// lockNamespace serialises requests that target the same mount namespace.
func (s *Server) lockNamespace(nsFile *os.File) func() {
	key := namespaceKey(nsFile)
	value, _ := s.nsLocks.LoadOrStore(key, &sync.Mutex{})
	mu := value.(*sync.Mutex)
	mu.Lock()
	return mu.Unlock
}

func namespaceKey(nsFile *os.File) string {
	var st syscall.Stat_t
	if err := syscall.Fstat(int(nsFile.Fd()), &st); err != nil {
		log.Errorf("Failed to stat namespace fd: %v", err)
		// Fall back to a single lock rather than running unserialised.
		return "unknown"
	}
	return fmt.Sprintf("%d:%d", st.Dev, st.Ino)
}
