package common

import (
	"fmt"
	"net"
	"os"
	"syscall"
)

// MaxMessageSize bounds the JSON payload exchanged over the server socket.
const MaxMessageSize = 8192

// LegacyResponseSize is the response buffer of shims that read with a single
// fixed-size recvmsg.
const LegacyResponseSize = 500

// SendFds writes msg to conn, passing fds alongside it via SCM_RIGHTS.
func SendFds(conn *net.UnixConn, fds []int, msg []byte) error {
	connf, err := conn.File()
	if err != nil {
		return fmt.Errorf("failed to get socket file: %w", err)
	}
	defer connf.Close()

	var rights []byte
	if len(fds) > 0 {
		rights = syscall.UnixRights(fds...)
	}
	return syscall.Sendmsg(int(connf.Fd()), msg, rights, nil, 0)
}

// RecvFds reads a message from conn together with up to maxFds descriptors,
// which the caller owns. No MSG_CMSG_CLOEXEC: fusermount-wrapper passes the
// mounted descriptor to its child by number.
func RecvFds(conn *net.UnixConn, maxFds int) ([]int, []byte, error) {
	connf, err := conn.File()
	if err != nil {
		return nil, nil, fmt.Errorf("failed to get socket file: %w", err)
	}
	defer connf.Close()

	oob := make([]byte, syscall.CmsgSpace(4*maxFds))
	buf := make([]byte, MaxMessageSize)
	//nolint:dogsled
	n, oobn, _, _, err := syscall.Recvmsg(int(connf.Fd()), buf, oob, 0)
	if err != nil {
		return nil, nil, fmt.Errorf("recvmsg failed: %w", err)
	}

	fds, err := parseRights(oob[:oobn])
	if err != nil {
		return nil, nil, err
	}
	return fds, buf[:n], nil
}

// WrapFds turns raw descriptors into files that own them.
func WrapFds(fds []int) []*os.File {
	files := make([]*os.File, 0, len(fds))
	for i, fd := range fds {
		files = append(files, os.NewFile(uintptr(fd), fmt.Sprintf("recvfd%d", i)))
	}
	return files
}

// parseRights collects every descriptor in the control message.
func parseRights(oob []byte) ([]int, error) {
	if len(oob) == 0 {
		return nil, nil
	}
	msgs, err := syscall.ParseSocketControlMessage(oob)
	if err != nil {
		return nil, fmt.Errorf("failed to parse socket control message: %w", err)
	}
	var fds []int
	for i := range msgs {
		parsed, err := syscall.ParseUnixRights(&msgs[i])
		if err != nil {
			CloseFds(fds)
			return nil, fmt.Errorf("failed to parse SCM_RIGHTS: %w", err)
		}
		fds = append(fds, parsed...)
	}
	return fds, nil
}

// CloseFds closes raw descriptors.
func CloseFds(fds []int) {
	for _, fd := range fds {
		_ = syscall.Close(fd)
	}
}

// CloseFiles closes every non-nil file in files.
func CloseFiles(files []*os.File) {
	for _, f := range files {
		if f != nil {
			_ = f.Close()
		}
	}
}
