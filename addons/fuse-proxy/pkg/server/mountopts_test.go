package server

import (
	"strings"
	"testing"

	"github.com/skypilot-org/skypilot/addons/fuse-proxy/pkg/common"
)

// TestMountFlagConstants pins each flag to its canonical Linux MS_* value.
// The constants are written as literals (mountopts.go avoids importing unix so
// it builds on any platform), so an off-by-one bit is only caught here. In
// particular MS_NOSYMFOLLOW is 0x100; 0x2000 is MS_MOVE.
func TestMountFlagConstants(t *testing.T) {
	want := map[string]uintptr{
		"MS_RDONLY":      0x1,
		"MS_NOSUID":      0x2,
		"MS_NODEV":       0x4,
		"MS_NOEXEC":      0x8,
		"MS_SYNCHRONOUS": 0x10,
		"MS_DIRSYNC":     0x80,
		"MS_NOSYMFOLLOW": 0x100,
		"MS_NOATIME":     0x400,
		"MS_NODIRATIME":  0x800,
		"MS_RELATIME":    0x200000,
		"MS_STRICTATIME": 0x1000000,
	}
	got := map[string]uintptr{
		"MS_RDONLY":      msRdonly,
		"MS_NOSUID":      msNosuid,
		"MS_NODEV":       msNodev,
		"MS_NOEXEC":      msNoexec,
		"MS_SYNCHRONOUS": msSynchronous,
		"MS_DIRSYNC":     msDirsync,
		"MS_NOSYMFOLLOW": msNosymfollow,
		"MS_NOATIME":     msNoatime,
		"MS_NODIRATIME":  msNodiratime,
		"MS_RELATIME":    msRelatime,
		"MS_STRICTATIME": msStrictatime,
	}
	for name, w := range want {
		if got[name] != w {
			t.Errorf("%s = %#x, want %#x", name, got[name], w)
		}
	}
}

func TestBuildMountParamsForcesSafeFlags(t *testing.T) {
	// suid and dev are ignored and the flags stay set.
	for _, options := range [][]string{
		{},
		{"suid"},
		{"dev"},
		{"suid", "dev"},
		{"allow_other", "suid", "dev", "ro"},
	} {
		params, err := BuildMountParams(options)
		if err != nil {
			t.Fatalf("BuildMountParams(%q) failed: %v", options, err)
		}
		if params.Flags&msNosuid == 0 {
			t.Errorf("BuildMountParams(%q): MS_NOSUID is not set", options)
		}
		if params.Flags&msNodev == 0 {
			t.Errorf("BuildMountParams(%q): MS_NODEV is not set", options)
		}
	}
}

func TestBuildMountParamsRejectsUnsafeOptions(t *testing.T) {
	for _, options := range [][]string{
		{"blkdev"},
		{"allow_root"},
		{"nosuid", "blkdev"},
		{"definitely_not_an_option"},
		{"max_read=abc"},
		{"max_read"},
		{"blksize=1k"},
		{"fsname=bad\nname"},
	} {
		if _, err := BuildMountParams(options); err == nil {
			t.Errorf("BuildMountParams(%q) succeeded, want error", options)
		}
	}
}

func TestBuildMountParamsFlags(t *testing.T) {
	params, err := BuildMountParams([]string{"ro", "noatime", "sync", "dirsync"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for name, flag := range map[string]uintptr{
		"MS_RDONLY":      msRdonly,
		"MS_NOATIME":     msNoatime,
		"MS_SYNCHRONOUS": msSynchronous,
		"MS_DIRSYNC":     msDirsync,
	} {
		if params.Flags&flag == 0 {
			t.Errorf("%s is not set", name)
		}
	}

	// rw clears the read-only flag the way fusermount does.
	params, err = BuildMountParams([]string{"ro", "rw"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if params.Flags&msRdonly != 0 {
		t.Error("rw did not clear MS_RDONLY")
	}
}

func TestBuildMountParamsKernelOptions(t *testing.T) {
	params, err := BuildMountParams([]string{
		"allow_other", "default_permissions", "max_read=131072",
		// Rebuilt by the helper, so a caller-supplied value is dropped.
		"fd=7", "rootmode=40000", "user_id=1000", "group_id=1000",
		"nonempty",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for _, dropped := range []string{"fd=", "rootmode=", "user_id=", "group_id=", "nonempty"} {
		if strings.Contains(params.KernelOpts, dropped) {
			t.Errorf("kernel options %q still carry %q", params.KernelOpts, dropped)
		}
	}
	for _, kept := range []string{"allow_other", "default_permissions", "max_read=131072"} {
		if !strings.Contains(params.KernelOpts, kept) {
			t.Errorf("kernel options %q dropped %q", params.KernelOpts, kept)
		}
	}
}

func TestBuildMountParamsSourceAndType(t *testing.T) {
	params, err := BuildMountParams(nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if params.Source != "/dev/fuse" || params.Type != "fuse" {
		t.Errorf("defaults are source=%q type=%q", params.Source, params.Type)
	}

	params, err = BuildMountParams([]string{"fsname=my-bucket", "subtype=gcsfuse"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if params.Source != "my-bucket" {
		t.Errorf("source = %q, want my-bucket", params.Source)
	}
	if params.Type != "fuse.gcsfuse" {
		t.Errorf("type = %q, want fuse.gcsfuse", params.Type)
	}
	// fsname and subtype describe the mount; they are not kernel options.
	if strings.Contains(params.KernelOpts, "fsname") ||
		strings.Contains(params.KernelOpts, "subtype") {
		t.Errorf("kernel options %q should not carry fsname or subtype", params.KernelOpts)
	}

	params, err = BuildMountParams([]string{`fsname=a\,b`})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if params.Source != "a,b" {
		t.Errorf("source = %q, want a,b", params.Source)
	}
}

func TestResolveRequest(t *testing.T) {
	// Only Args set.
	got, err := resolveRequest(&common.Request{
		Args: []string{"-o", "allow_other", "/mnt/data"},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.unmount || got.mountpoint != "/mnt/data" || got.hasTargetFD {
		t.Errorf("legacy mount parsed as %+v", got)
	}
	if len(got.options) != 1 || got.options[0] != "allow_other" {
		t.Errorf("legacy mount options = %q", got.options)
	}

	got, err = resolveRequest(&common.Request{Args: []string{"-uz", "/mnt/data"}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !got.unmount || !got.lazy || got.mountpoint != "/mnt/data" {
		t.Errorf("legacy unmount parsed as %+v", got)
	}

	// A current shim sends the parsed request and the mountpoint fd.
	got, err = resolveRequest(&common.Request{
		Args:        []string{"-o", "ro", "/mnt/data"},
		Op:          common.OpMount,
		Mountpoint:  "/mnt/data",
		Options:     []string{"ro"},
		HasTargetFD: true,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.unmount || !got.hasTargetFD || got.mountpoint != "/mnt/data" {
		t.Errorf("structured mount parsed as %+v", got)
	}

	if _, err := resolveRequest(&common.Request{Op: "something-else"}); err == nil {
		t.Error("unknown op should be rejected")
	}
	if _, err := resolveRequest(&common.Request{Args: []string{"-x"}}); err == nil {
		t.Error("unparsable legacy args should be rejected")
	}
}

func TestBuildMountParamsSafeAtimeAndSymlinkFlags(t *testing.T) {
	// libfuse marks these safe; before the allowlist they reached the real
	// fusermount, so a client passing them must still mount.
	cases := map[string]struct {
		set   uintptr
		clear uintptr
	}{
		"nodiratime":    {set: msNodiratime},
		"nosymfollow":   {set: msNosymfollow},
		"norelatime":    {clear: msRelatime},
		"nostrictatime": {clear: msStrictatime},
		"symfollow":     {clear: msNosymfollow},
	}
	for opt, want := range cases {
		params, err := BuildMountParams([]string{opt})
		if err != nil {
			t.Fatalf("BuildMountParams(%q) failed: %v", opt, err)
		}
		if want.set != 0 && params.Flags&want.set == 0 {
			t.Errorf("%s did not set its flag", opt)
		}
		if want.clear != 0 && params.Flags&want.clear != 0 {
			t.Errorf("%s did not clear its flag", opt)
		}
		if params.Flags&msNosuid == 0 || params.Flags&msNodev == 0 {
			t.Errorf("%s dropped the forced nosuid/nodev flags", opt)
		}
	}
}

func TestBuildMountParamsSkipsXOptions(t *testing.T) {
	params, err := BuildMountParams([]string{"x-systemd.automount", "allow_other", "x-gvfs-show"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.Contains(params.KernelOpts, "x-") {
		t.Errorf("x- options leaked into kernel options: %q", params.KernelOpts)
	}
	if !strings.Contains(params.KernelOpts, "allow_other") {
		t.Errorf("allow_other dropped alongside x- options: %q", params.KernelOpts)
	}
}
