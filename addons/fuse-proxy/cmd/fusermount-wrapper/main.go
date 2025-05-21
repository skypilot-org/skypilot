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
	"os/signal"
	"syscall"
	"time"

	daemon "github.com/sevlyar/go-daemon"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/client"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	flag "github.com/spf13/pflag"
	log "k8s.io/klog/v2"
)

const (
	mountPointDescription  = "the path to the mount point of the FUSE device"
	placeholderDescription = "the placeholder to replace the mount point in fuse implementation command"
	optionsDescription     = "mount options"
	daemonizeDescription   = "daemonize the fuser adapter process"

	// The timeout to wait for the fuse adapter to be ready, same as the mount timeout of azure blobfuse2.
	waitMountTimeout = 5 * time.Second
)

var (
	mountPoint   = flag.StringP("mount-point", "m", "", mountPointDescription)
	placeholder  = flag.StringP("placeholder", "p", "{}", placeholderDescription)
	mountOptions = flag.StringP("options", "o", "", optionsDescription)
	daemonize    = flag.BoolP("daemonize", "d", false, "daemonize the fuser adapter process")
)

func usage() {
	fmt.Printf(`Usage: %s -m /path/to/mount-point -- FUSE_IMPL_CMD [FUSE_IMPL_FLAGS] {}
Options:
-m              %s
-p              %s
-o opt[,opt...] %s
-d
`, os.Args[0], mountPointDescription, placeholderDescription, optionsDescription)
}

func mount() {
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
	// Execute the command using cmd.Run() instead of syscall.Exec
	// This ensures ExtraFiles gets properly passed to the child process
	cmd := exec.Command(fuseCmd[0], fuseCmd[1:]...)
	cmd.ExtraFiles = []*os.File{os.NewFile(uintptr(fd), "/dev/fuse")}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		log.Errorf("Failed to run fuse adapter: %v, try to unmount %s", err, *mountPoint)
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

	if *daemonize {
		// Daemonize the fuse adapter process
		fname := fmt.Sprintf("/tmp/fusermount-wrapper.%v", os.Getpid())
		dmnCtx := &daemon.Context{
			Umask:       0o22,
			LogFileName: fname,
		}
		var sigChild chan os.Signal
		if !daemon.WasReborn() {
			// Setup signal handler in parent process
			sigChild = make(chan os.Signal, 1)
			signal.Notify(sigChild, syscall.SIGCHLD)
		}
		child, err := dmnCtx.Reborn()
		if err != nil {
			log.Fatalf("Failed to daemonize fuse adapter: %v", err)
		}
		if child != nil {
			// Only runs in parent process: fusermount-wrapper monitors the fuse adapter
			readyiness := time.After(waitMountTimeout)
			ticker := time.NewTicker(100 * time.Millisecond)
			defer ticker.Stop()

			log.Infof("Waiting for fuse adapter running")
			select {
			case <-sigChild:
				buff, err := os.ReadFile(dmnCtx.LogFileName)
				if err != nil {
					log.Errorf("Fuse adapter process exited: failed to read child [%v] failure logs [%s]", child.Pid, err.Error())
				} else {
					log.Errorf("Fuse adapter process exited: failure logs: %s", string(buff))
				}
				os.Exit(1)
			case <-readyiness:
				// TODO(aylei): we assume the fuse is ready if the fuse adapter process does not exit for 5 seconds
				// because blobfuse2 has the same mount timeout. But this is hacky and might not work for other fuse adapters.
				// We need to check the fuse is ready by calling fusermount-server.
				log.Infof("Fuse adapter is ready")
				os.Exit(0)
			}
		} else {
			defer func() {
				_ = dmnCtx.Release()
			}()
			// Only runs in child process (daemonized): start the fuse adapter
			mount()
		}
	} else {
		log.Infof("Running fuse adapter directly")
		mount()
	}
}
