package server

import (
	"fmt"
	"strings"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
	log "k8s.io/klog/v2"
)

// Linux mount flag values. They are written out rather than taken from
// golang.org/x/sys/unix so that this file and its tests build on any platform.
const (
	msRdonly      uintptr = 0x1
	msNosuid      uintptr = 0x2
	msNodev       uintptr = 0x4
	msNoexec      uintptr = 0x8
	msSynchronous uintptr = 0x10
	msDirsync     uintptr = 0x80
	msNoatime     uintptr = 0x400
	msNodiratime  uintptr = 0x800
	msRelatime    uintptr = 0x200000
	msStrictatime uintptr = 0x1000000
	msNosymfollow uintptr = 0x2000
)

// maxFsnameLen bounds fsname= and subtype= values, which become the source and
// type arguments of mount(2).
const maxFsnameLen = 255

// mountFlag mirrors an entry of libfuse's mount_flags table in
// util/fusermount.c.
type mountFlag struct {
	flag uintptr
	// on reports whether naming the option sets the flag or clears it.
	on bool
	// safe reports whether libfuse honours the option for a caller that is
	// not root.
	safe bool
}

var mountFlags = map[string]mountFlag{
	"rw":            {msRdonly, false, true},
	"ro":            {msRdonly, true, true},
	"suid":          {msNosuid, false, false},
	"nosuid":        {msNosuid, true, true},
	"dev":           {msNodev, false, false},
	"nodev":         {msNodev, true, true},
	"exec":          {msNoexec, false, true},
	"noexec":        {msNoexec, true, true},
	"async":         {msSynchronous, false, true},
	"sync":          {msSynchronous, true, true},
	"atime":         {msNoatime, false, true},
	"noatime":       {msNoatime, true, true},
	"nodiratime":    {msNodiratime, true, true},
	"norelatime":    {msRelatime, false, true},
	"nostrictatime": {msStrictatime, false, true},
	"symfollow":     {msNosymfollow, false, true},
	"nosymfollow":   {msNosymfollow, true, true},
	"dirsync":       {msDirsync, true, true},
}

// passthroughOpts are the options fusermount hands to the kernel untouched.
var passthroughOpts = map[string]bool{
	"default_permissions": true,
	"allow_other":         true,
}

// droppedOpts are options fusermount consumes without passing them to the
// kernel. fd, rootmode, user_id and group_id are rebuilt by the helper from
// values it trusts, so whatever the caller supplied is discarded.
var droppedOpts = map[string]bool{
	"nonempty": true,
	"fd":       true,
	"rootmode": true,
	"user_id":  true,
	"group_id": true,
}

// numericOpts are passed to the kernel but only with a numeric value.
var numericOpts = map[string]bool{
	"max_read": true,
	"blksize":  true,
}

// MountParams holds the mount(2) arguments for the helper.
type MountParams struct {
	Flags      uintptr
	KernelOpts string
	Source     string
	Type       string
}

// BuildMountParams turns mount options into mount(2) arguments, accepting what
// fusermount accepts from a non-root caller. MS_NOSUID and MS_NODEV are always
// set.
func BuildMountParams(options []string) (*MountParams, error) {
	flags := msNosuid | msNodev
	var kernelOpts []string
	var fsname, subtype string

	for _, opt := range options {
		name, value, hasValue := strings.Cut(opt, "=")

		if f, ok := mountFlags[name]; ok && !hasValue {
			if !f.safe {
				// libfuse prints "unsafe option %s ignored" and
				// carries on when the caller is not root.
				log.Warningf("Ignoring unsafe mount option %q", name)
				continue
			}
			if f.on {
				flags |= f.flag
			} else {
				flags &^= f.flag
			}
			continue
		}

		switch {
		case name == "fsname" && hasValue:
			fsname = common.UnescapeOptionValue(value)
			if err := validateFsString("fsname", fsname); err != nil {
				return nil, err
			}
		case name == "subtype" && hasValue:
			subtype = common.UnescapeOptionValue(value)
			if err := validateFsString("subtype", subtype); err != nil {
				return nil, err
			}
		case name == "blkdev":
			// fusermount refuses this for a non-root caller.
			return nil, fmt.Errorf("mount option %q is privileged", name)
		case name == "auto_unmount":
			// Only meaningful to the fusermount process that would
			// have stayed alive to perform it.
			log.Warningf("Ignoring unsupported mount option %q", name)
		case droppedOpts[name]:
			// Consumed, exactly as fusermount consumes them.
		case numericOpts[name]:
			if !hasValue || !isDigits(value) {
				return nil, fmt.Errorf("mount option %q needs a numeric value", name)
			}
			kernelOpts = append(kernelOpts, name+"="+value)
		case passthroughOpts[name] && !hasValue:
			kernelOpts = append(kernelOpts, name)
		case strings.HasPrefix(name, "x-"):
			// Userspace-only options (e.g. x-systemd.*); fusermount
			// strips them before validating and never passes them to
			// the kernel.
		default:
			return nil, fmt.Errorf("unknown mount option %q", opt)
		}
	}

	params := &MountParams{
		Flags:      flags,
		KernelOpts: strings.Join(kernelOpts, ","),
		Source:     "/dev/fuse",
		Type:       "fuse",
	}
	if fsname != "" {
		params.Source = fsname
	}
	if subtype != "" {
		params.Type = "fuse." + subtype
	}
	return params, nil
}

// validateFsString rejects values that would be ambiguous once they appear in
// mount(2) arguments and in /proc/mounts.
func validateFsString(name, value string) error {
	if len(value) > maxFsnameLen {
		return fmt.Errorf("mount option %q is too long", name)
	}
	for i := 0; i < len(value); i++ {
		if value[i] < 0x20 || value[i] == 0x7f {
			return fmt.Errorf("mount option %q contains a control character", name)
		}
	}
	return nil
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}
