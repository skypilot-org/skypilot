package common

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstallBinaryReplacesSymlink(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "src")
	if err := os.WriteFile(src, []byte("new binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(dir, "victim")
	if err := os.WriteFile(victim, []byte("untouched"), 0o644); err != nil {
		t.Fatal(err)
	}
	dst := filepath.Join(dir, "fusermount-shim")
	if err := os.Symlink(victim, dst); err != nil {
		t.Fatal(err)
	}

	if err := InstallBinary(src, dst); err != nil {
		t.Fatalf("InstallBinary failed: %v", err)
	}

	got, err := os.ReadFile(victim)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "untouched" {
		t.Errorf("symlink target was written: %q", got)
	}
	info, err := os.Lstat(dst)
	if err != nil {
		t.Fatal(err)
	}
	if !info.Mode().IsRegular() {
		t.Errorf("dst is %v, want a regular file", info.Mode())
	}
	if info.Mode().Perm() != 0o755 {
		t.Errorf("dst mode = %v, want 0755", info.Mode().Perm())
	}
	got, err = os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "new binary" {
		t.Errorf("dst content = %q", got)
	}
}

func TestInstallBinaryOverwritesExisting(t *testing.T) {
	dir := t.TempDir()
	src := filepath.Join(dir, "src")
	dst := filepath.Join(dir, "dst")
	if err := os.WriteFile(src, []byte("v2"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(dst, []byte("v1"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := InstallBinary(src, dst); err != nil {
		t.Fatalf("InstallBinary failed: %v", err)
	}
	got, err := os.ReadFile(dst)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "v2" {
		t.Errorf("dst content = %q, want v2", got)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Errorf("temporary file left behind: %d entries", len(entries))
	}
}
