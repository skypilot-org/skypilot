// fusermount-wrapper is designed to work with fuse adapters that use libfuse
// to mount the FUSE device directly.
// It wraps the fuse adapter process and:
//  1. call fusermount-server to mount the FUSE device before executing the fuse
//     adapter commands;
//  2. rewrite the fuse adapter commands to use the mounted fd (/dev/fd/N),
//     so that libfuse will bypass /dev/fuse access and use the mounted fd instead.
//     ref: https://github.com/libfuse/libfuse/blob/master/lib/fuse_lowlevel.c
//
// Note that fusermount-wrapper does not unmount the FUSE device since libfuse will
// properly fallback to fusermount3 to do unmount, which will be handled by
// fusermount-shim eventually.
package main

import (
	"fmt"
	"os"
	"os/exec"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/client"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	flag "github.com/spf13/pflag"
	log "k8s.io/klog/v2"
)

const (
	mountPointDescription  = "the path to the mount point of the FUSE device"
	placeholderDescription = "the placeholder to replace the mount point in fuse implementation command"
	optionsDescription     = "mount options"
)

var (
	mountPoint   = flag.StringP("mount-point", "m", "", mountPointDescription)
	placeholder  = flag.StringP("placeholder", "p", "{}", placeholderDescription)
	mountOptions = flag.StringP("options", "o", "", optionsDescription)
)

func usage() {
	fmt.Printf(`Usage: %s -m /path/to/mount-point -- FUSE_IMPL_CMD [FUSE_IMPL_FLAGS] {}
Options:
-m              %s
-p              %s
-o opt[,opt...] %s
`, os.Args[0], mountPointDescription, placeholderDescription, optionsDescription)
}

func main() {
	log.InitFlags(nil)
	flag.Parse()
	if *mountPoint == "" {
		usage()
		log.Fatalf("mount-point is required")
	}
	if flag.NArg() == 0 {
		usage()
		log.Fatalf("fuse implementation command is required")
	}
	socketPath := common.MustGetServerSocketPath()
	// Forward the fusermount request to the server and receive the mounted fd
	c, err := client.NewClient(socketPath)
	if err != nil {
		log.Fatalf("Failed to connect to fusermount server at %s: %v", socketPath, err)
	}
	defer c.Close()
	fusermountArgs := []string{*mountPoint}
	if *mountOptions != "" {
		fusermountArgs = append(fusermountArgs, "-o", *mountOptions)
	}
	fd, err := c.Fusermount(fusermountArgs)
	if err != nil {
		log.Fatalf("An error occurred when calling fusermount server: %v", err)
	}
	fuseCmd := flag.Args()
	for i := range fuseCmd {
		if fuseCmd[i] == *placeholder {
			fuseCmd[i] = fmt.Sprintf("/dev/fd/%d", fd)
		}
	}
	log.Infof("FUSE command: %v", fuseCmd)
	cmd := exec.Command(fuseCmd[0], fuseCmd[1:]...)
	cmd.ExtraFiles = []*os.File{os.NewFile(uintptr(fd), "/dev/fuse")}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	// Execute the command using cmd.Run() instead of syscall.Exec
	// This ensures ExtraFiles gets properly passed to the child process
	if err := cmd.Run(); err != nil {
		// Unmount the FUSE device if FUSE command failed
		unmountCmd := exec.Command("fusermount", "-u", *mountPoint)
		if unmountErr := unmountCmd.Run(); unmountErr != nil {
			log.Warningf("Failed to unmount %s: %v", *mountPoint, unmountErr)
		} else {
			log.Infof("Successfully unmounted %s", *mountPoint)
		}
		// Get the exit code from the command and exit with the same code
		if exitErr, ok := err.(*exec.ExitError); ok {
			os.Exit(exitErr.ExitCode())
		} else {
			// Unknown error
			log.Fatalf("Failed to run %s: %v", cmd.Path, err)
		}
	}
}
