package common

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// InstallBinary copies src to dst via rename, replacing rather than following
// a symlink at dst.
func InstallBinary(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return fmt.Errorf("failed to open %s: %w", src, err)
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil {
		return fmt.Errorf("failed to stat %s: %w", src, err)
	}

	tmp, err := os.CreateTemp(filepath.Dir(dst), "."+filepath.Base(dst)+".tmp-*")
	if err != nil {
		return fmt.Errorf("failed to create temporary file for %s: %w", dst, err)
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)

	if _, err := io.Copy(tmp, in); err != nil {
		tmp.Close()
		return fmt.Errorf("failed to copy %s: %w", src, err)
	}
	if err := tmp.Chmod(info.Mode().Perm()); err != nil {
		tmp.Close()
		return fmt.Errorf("failed to set mode on %s: %w", tmpPath, err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("failed to write %s: %w", tmpPath, err)
	}
	if err := os.Rename(tmpPath, dst); err != nil {
		return fmt.Errorf("failed to install %s: %w", dst, err)
	}
	return nil
}
