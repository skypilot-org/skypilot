package server

import "strings"

func isUmount(args []string) bool {
	for _, arg := range args {
		if strings.HasPrefix(arg, "-") && strings.Contains(arg, "u") {
			return true
		}
		if arg == "--unmount" {
			return true
		}
	}
	return false
}
