package server

import (
	"encoding/json"
	"net"
	"os"
	"syscall"
	"testing"

	mfcputil "github.com/pfnet-research/meta-fuse-csi-plugin/pkg/util"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
)

func socketPair(t *testing.T) (*net.UnixConn, *net.UnixConn) {
	t.Helper()
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		t.Fatal(err)
	}
	conns := make([]*net.UnixConn, 2)
	for i, fd := range fds {
		f := os.NewFile(uintptr(fd), "sock")
		c, err := net.FileConn(f)
		f.Close()
		if err != nil {
			t.Fatal(err)
		}
		conns[i] = c.(*net.UnixConn)
	}
	return conns[0], conns[1]
}

// Older shims read every response with a single-descriptor recvmsg.
func TestErrorResponseReadableByLegacyShim(t *testing.T) {
	serverConn, clientConn := socketPair(t)
	defer clientConn.Close()
	done := make(chan struct{})
	go func() {
		NewServer("/nonexistent-helper", "").handleConnection(serverConn)
		close(done)
	}()

	if err := common.SendFds(clientConn, nil, []byte(`{"args":["-u","/mnt/data"]}`)); err != nil {
		t.Fatal(err)
	}
	fd, msg, err := mfcputil.RecvMsg(clientConn)
	if err != nil {
		t.Fatalf("legacy receive failed: %v", err)
	}
	syscall.Close(fd)
	resp := &common.Response{}
	if err := json.Unmarshal(msg, resp); err != nil {
		t.Fatal(err)
	}
	if resp.Success || resp.Error == "" {
		t.Errorf("response = %+v, want a failure with its reason", resp)
	}
	<-done
}
