// fusermount-shim is a binary that replaces the fusermount binary[1] in a non-privileged container and
// forwards the fusermount request to the fusermount server running in the privileged container.
//
// [1]: https://man7.org/linux/man-pages/man1/fusermount3.1.html
package main

import (
	"fmt"
	"io"
	"net"
	"os"

	"github.com/pfnet-research/meta-fuse-csi-plugin/pkg/util"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/client"
	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	flag "github.com/spf13/pflag"
	log "k8s.io/klog/v2"
)

const (
	helpDescription    = "print help"
	versionDescription = "print version"
	unmountDescription = "unmount"
	quietDescription   = "quiet"
	lazyDescription    = "lazy unmount"
	optionsDescription = "mount options"
)

var (
	printHelp    = flag.BoolP("help", "h", false, helpDescription)
	printVersion = flag.BoolP("version", "V", false, versionDescription)
	// Parse unmount flag to determine if the operation is a mount or unmount
	unmount = flag.BoolP("unmount", "u", false, unmountDescription)
	// Unused flags, to make pflag parse flags like -uz correctly
	_ = flag.BoolP("quiet", "q", false, quietDescription)
	_ = flag.BoolP("lazy", "z", false, lazyDescription)
	_ = flag.StringP("options", "o", "", optionsDescription)
)

func usage() {
	// Usage copied from libfuse:
	// https://github.com/libfuse/libfuse/blob/1b86fe4c4de96daa4e766425193595f1c6b88a73/util/fusermount.c#L1435
	fmt.Printf(`%s: [options] mountpoint
Options:
 -h		    %s
 -V		    %s
 -o opt[,opt...]    %s
 -u		    %s
 -q		    %s
 -z		    %s
`, os.Args[0], helpDescription, versionDescription, optionsDescription, unmountDescription, quietDescription, lazyDescription)
}

func main() {
	log.InitFlags(nil)
	// Set custom error handling to ignore unknown flags
	flag.CommandLine.Init(os.Args[0], flag.ContinueOnError)
	flag.CommandLine.Usage = usage
	flag.CommandLine.SetOutput(io.Discard)
	flag.Parse()
	if *printHelp {
		usage()
		os.Exit(0)
	}
	if *printVersion {
		// TODO(aylei): use git tag as version
		fmt.Println("fusermount3-shim version: 0.1.0")
		os.Exit(0)
	}
	if len(flag.Args()) == 0 {
		fmt.Println("mountpoint must be specified.")
		usage()
		os.Exit(1)
	}
	isMount := !*unmount
	var commFd int
	if isMount {
		// Caller must set _FUSE_COMMFD for mount operation
		commFd = common.MustGetFuseCommFD()
	}
	socketPath := common.MustGetServerSocketPath()
	// Forward the fusermount request to the server and receive the mounted fd
	c, err := client.NewClient(socketPath)
	if err != nil {
		log.Fatalf("Failed to connect to fusermount server at %s: %v", socketPath, err)
	}
	defer c.Close()
	fd, err := c.Fusermount(os.Args[1:])
	if err != nil {
		log.Fatalf("An error occurred when calling fusermount server: %v", err)
	}
	// For mount operation, send the received fd back to caller (the FUSE implementation)
	if isMount {
		f := os.NewFile(uintptr(commFd), "unix_socket")
		defer f.Close()
		conn, err := net.FileConn(f)
		defer conn.Close()
		if err != nil {
			log.Fatalf("Failed to connect to _FUSE_COMMFD %d: %v", commFd, err)
		}
		util.SendMsg(conn, fd, []byte{})
	}
}
