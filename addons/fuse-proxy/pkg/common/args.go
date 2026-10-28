package common

import (
	"fmt"
	"strings"
)

// FusermountArgs is a parsed fusermount command line.
type FusermountArgs struct {
	Unmount    bool
	Lazy       bool
	Quiet      bool
	Help       bool
	Version    bool
	Mountpoint string
	Options    []string
}

// ParseFusermountArgs parses a fusermount(1) command line the way libfuse
// does: short flags cluster, -o takes its value attached or as the next
// argument, and the first non-flag argument is the mountpoint.
func ParseFusermountArgs(args []string) (*FusermountArgs, error) {
	out := &FusermountArgs{}
	var positional []string
	endOfFlags := false

	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case endOfFlags || arg == "" || arg == "-" || !strings.HasPrefix(arg, "-"):
			positional = append(positional, arg)
		case arg == "--":
			endOfFlags = true
		case strings.HasPrefix(arg, "--"):
			name, value, hasValue := strings.Cut(arg[2:], "=")
			switch name {
			case "unmount":
				out.Unmount = true
			case "lazy":
				out.Lazy = true
			case "quiet":
				out.Quiet = true
			case "help":
				out.Help = true
			case "version":
				out.Version = true
			case "options":
				if !hasValue {
					i++
					if i >= len(args) {
						return nil, fmt.Errorf("option %q requires a value", arg)
					}
					value = args[i]
				}
				out.Options = append(out.Options, SplitOptions(value)...)
			default:
				return nil, fmt.Errorf("unknown option %q", arg)
			}
		default:
			rest := arg[1:]
			for rest != "" {
				c := rest[0]
				rest = rest[1:]
				switch c {
				case 'u':
					out.Unmount = true
				case 'z':
					out.Lazy = true
				case 'q':
					out.Quiet = true
				case 'h':
					out.Help = true
				case 'V':
					out.Version = true
				case 'o':
					value := rest
					rest = ""
					if value == "" {
						i++
						if i >= len(args) {
							return nil, fmt.Errorf("option %q requires a value", arg)
						}
						value = args[i]
					}
					out.Options = append(out.Options, SplitOptions(value)...)
				default:
					return nil, fmt.Errorf("unknown option %q in %q", string(c), arg)
				}
			}
		}
	}

	if len(positional) > 0 {
		out.Mountpoint = positional[0]
	}
	return out, nil
}

// SplitOptions splits a -o value on commas. Inside fsname= and subtype=
// values a backslash escapes the next character, as libfuse allows.
func SplitOptions(s string) []string {
	var out []string
	for s != "" {
		escapeOK := strings.HasPrefix(s, "fsname=") ||
			strings.HasPrefix(s, "subtype=")
		n := 0
		for n < len(s) {
			if escapeOK && s[n] == '\\' && n+1 < len(s) {
				n += 2
				continue
			}
			if s[n] == ',' {
				break
			}
			n++
		}
		if n > len(s) {
			n = len(s)
		}
		if opt := s[:n]; opt != "" {
			out = append(out, opt)
		}
		if n >= len(s) {
			break
		}
		s = s[n+1:]
	}
	return out
}

// UnescapeOptionValue removes the backslash escapes libfuse allows in fsname=
// and subtype= values.
func UnescapeOptionValue(s string) string {
	if !strings.Contains(s, `\`) {
		return s
	}
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			i++
		}
		b.WriteByte(s[i])
	}
	return b.String()
}
