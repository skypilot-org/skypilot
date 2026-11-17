package common

import (
	"reflect"
	"testing"
)

func TestParseFusermountArgs(t *testing.T) {
	tests := []struct {
		name       string
		args       []string
		unmount    bool
		lazy       bool
		mountpoint string
		options    []string
	}{{
		name:       "mount with options",
		args:       []string{"-o", "allow_other,default_permissions", "--", "/home/sky/bucket"},
		mountpoint: "/home/sky/bucket",
		options:    []string{"allow_other", "default_permissions"},
	}, {
		name:       "attached option value",
		args:       []string{"-oallow_other", "/mnt/data"},
		mountpoint: "/mnt/data",
		options:    []string{"allow_other"},
	}, {
		name:       "mountpoint before flags",
		args:       []string{"/mnt/data", "-o", "ro"},
		mountpoint: "/mnt/data",
		options:    []string{"ro"},
	}, {
		name:       "unmount",
		args:       []string{"-u", "/mnt/data"},
		unmount:    true,
		mountpoint: "/mnt/data",
	}, {
		name:       "unmount long form",
		args:       []string{"--unmount", "/mnt/data"},
		unmount:    true,
		mountpoint: "/mnt/data",
	}, {
		name:       "clustered unmount flags",
		args:       []string{"-uz", "/mnt/data"},
		unmount:    true,
		lazy:       true,
		mountpoint: "/mnt/data",
	}, {
		name:       "option value containing u is not an unmount",
		args:       []string{"-ouser_id=1000,group_id=1000", "/mnt/data"},
		mountpoint: "/mnt/data",
		options:    []string{"user_id=1000", "group_id=1000"},
	}, {
		// A bucket name containing "-u" is a mountpoint, not a flag.
		name:       "mountpoint containing -u",
		args:       []string{"-o", "allow_other", "/home/sky/my-upload-bucket"},
		mountpoint: "/home/sky/my-upload-bucket",
		options:    []string{"allow_other"},
	}, {
		name:       "escaped comma in fsname",
		args:       []string{"-o", `fsname=a\,b,allow_other`, "/mnt/data"},
		mountpoint: "/mnt/data",
		options:    []string{`fsname=a\,b`, "allow_other"},
	}, {
		name:       "repeated option flags accumulate",
		args:       []string{"-o", "ro", "-o", "allow_other", "/mnt/data"},
		mountpoint: "/mnt/data",
		options:    []string{"ro", "allow_other"},
	}, {
		name:       "mountpoint after end of flags marker",
		args:       []string{"--", "-weird-name"},
		mountpoint: "-weird-name",
	}}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ParseFusermountArgs(tc.args)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got.Unmount != tc.unmount {
				t.Errorf("unmount = %v, want %v", got.Unmount, tc.unmount)
			}
			if got.Lazy != tc.lazy {
				t.Errorf("lazy = %v, want %v", got.Lazy, tc.lazy)
			}
			if got.Mountpoint != tc.mountpoint {
				t.Errorf("mountpoint = %q, want %q", got.Mountpoint, tc.mountpoint)
			}
			if len(got.Options) != 0 || len(tc.options) != 0 {
				if !reflect.DeepEqual(got.Options, tc.options) {
					t.Errorf("options = %q, want %q", got.Options, tc.options)
				}
			}
		})
	}
}

func TestParseFusermountArgsErrors(t *testing.T) {
	for _, args := range [][]string{
		{"-x", "/mnt/data"},
		{"--bogus", "/mnt/data"},
		{"-o"},
		{"--options"},
	} {
		if _, err := ParseFusermountArgs(args); err == nil {
			t.Errorf("ParseFusermountArgs(%q) succeeded, want error", args)
		}
	}
}

func TestSplitOptions(t *testing.T) {
	tests := []struct {
		in   string
		want []string
	}{
		{"", nil},
		{"ro", []string{"ro"}},
		{"ro,allow_other", []string{"ro", "allow_other"}},
		{",ro,,allow_other,", []string{"ro", "allow_other"}},
		{`fsname=a\,b,ro`, []string{`fsname=a\,b`, "ro"}},
		{`subtype=x\,y`, []string{`subtype=x\,y`}},
		// A backslash only escapes inside fsname= and subtype=.
		{`max_read=1\,ro`, []string{`max_read=1\`, "ro"}},
		// A trailing backslash must not read past the end of the string.
		{`fsname=a\`, []string{`fsname=a\`}},
	}
	for _, tc := range tests {
		got := SplitOptions(tc.in)
		if len(got) == 0 && len(tc.want) == 0 {
			continue
		}
		if !reflect.DeepEqual(got, tc.want) {
			t.Errorf("SplitOptions(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestUnescapeOptionValue(t *testing.T) {
	tests := map[string]string{
		"plain":  "plain",
		`a\,b`:   "a,b",
		`a\\b`:   `a\b`,
		`trail\`: `trail\`,
	}
	for in, want := range tests {
		if got := UnescapeOptionValue(in); got != want {
			t.Errorf("UnescapeOptionValue(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestTruncateError(t *testing.T) {
	long := make([]byte, 1000)
	for i := range long {
		long[i] = 'x'
	}
	got := TruncateError(string(long))
	if len(got) >= LegacyResponseSize {
		t.Errorf("truncated error is %d bytes, must stay under %d", len(got), LegacyResponseSize)
	}
	if TruncateError("short") != "short" {
		t.Error("short errors must pass through unchanged")
	}
}
