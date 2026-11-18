package main

import (
	"flag"
	"os"
	"os/signal"
	"syscall"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/server"
	log "k8s.io/klog/v2"
)

var helperPath = flag.String("helper-path", common.HelperBinPath,
	"Path to the privileged mount helper in this container")

func main() {
	log.InitFlags(nil)
	flag.Parse()
	shimInstallPath := common.MustGetShimInstallPath()
	wrapperInstallPath := common.MustGetWrapperInstallPath()
	// Ensure the latest fusermount-shim and fusermount-wrapper are installed to
	// shared path so that the caller container can access it.
	// We assume the architecture of the caller container is the same as this
	// server container since they are running on the same node.
	if err := common.InstallBinary(common.ShimBinPath, shimInstallPath); err != nil {
		log.Fatalf("Failed to install fusermount-shim: %v", err)
	}
	if err := common.InstallBinary(common.WrapperBinPath, wrapperInstallPath); err != nil {
		log.Fatalf("Failed to install fusermount-wrapper: %v", err)
	}

	socketPath := common.MustGetServerSocketPath()

	srv := server.NewServer(*helperPath, socketPath)

	// Handle signals for graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	errChan := make(chan error, 1)
	go func() {
		errChan <- srv.Start()
	}()
	select {
	case err := <-errChan:
		if err != nil {
			log.Fatalf("Server error: %v", err)
		}
	case <-sigChan:
		log.Info("Shutting down server...")
		if err := srv.Stop(); err != nil {
			log.Errorf("Error during shutdown: %v", err)
		}
	}
}
