// sky-fuse-helper mounts and unmounts FUSE filesystems inside the caller's
// mount namespace on behalf of fusermount-server.
//
// It is a separate single-threaded process because setns(CLONE_NEWNS) requires
// an unshared fs_struct, which Go runtime threads never have. After setns it
// only makes system calls. Mount options arrive validated by the server.

#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#ifndef UMOUNT_NOFOLLOW
#define UMOUNT_NOFOLLOW 8
#endif
#ifndef MNT_DETACH
#define MNT_DETACH 2
#endif

#define FUSE_DEV_MAJOR 10
#define FUSE_DEV_MINOR 229

#define MOUNT_DATA_MAX 4096
#define MOUNTINFO_LINE_MAX 8192

static const char *progname = "sky-fuse-helper";

static void die_errno(const char *msg) {
	fprintf(stderr, "%s: %s: %s\n", progname, msg, strerror(errno));
	exit(1);
}

static void die(const char *msg) {
	fprintf(stderr, "%s: %s\n", progname, msg);
	exit(1);
}

static void usage(void) {
	fprintf(stderr,
		"usage: %s mount  --ns-fd=N --fuse-fd=N (--target-fd=N|--target=PATH)\n"
		"                 --flags=N --opts=STR --type=STR --source=STR\n"
		"                 --user-id=N --group-id=N [--ensure-dev]\n"
		"       %s umount --ns-fd=N --target=PATH [--lazy]\n",
		progname, progname);
	exit(2);
}

// opt_value returns the value of arg when it is "--<name>=<value>", else NULL.
static const char *opt_value(const char *arg, const char *name) {
	size_t n = strlen(name);
	if (strncmp(arg, "--", 2) != 0) {
		return NULL;
	}
	if (strncmp(arg + 2, name, n) != 0 || arg[2 + n] != '=') {
		return NULL;
	}
	return arg + 2 + n + 1;
}

static int parse_fd(const char *s, const char *what) {
	char *end = NULL;
	long v;

	errno = 0;
	v = strtol(s, &end, 10);
	if (errno != 0 || end == s || *end != '\0' || v < 0 || v > INT_MAX) {
		fprintf(stderr, "%s: invalid %s: %s\n", progname, what, s);
		exit(2);
	}
	return (int)v;
}

static unsigned long parse_ulong(const char *s, const char *what) {
	char *end = NULL;
	unsigned long v;

	errno = 0;
	v = strtoul(s, &end, 0);
	if (errno != 0 || end == s || *end != '\0') {
		fprintf(stderr, "%s: invalid %s: %s\n", progname, what, s);
		exit(2);
	}
	return v;
}

static void enter_mount_namespace(int ns_fd) {
	if (setns(ns_fd, CLONE_NEWNS) < 0) {
		die_errno("setns to target mount namespace");
	}
}

// ensure_fuse_device creates /dev/fuse in the caller's namespace when it is
// missing; some FUSE clients stat it before mounting.
static void ensure_fuse_device(void) {
	struct stat st;
	int devfd;

	devfd = open("/dev", O_PATH | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
	if (devfd < 0) {
		die_errno("open /dev in target namespace");
	}
	if (fstatat(devfd, "fuse", &st, AT_SYMLINK_NOFOLLOW) == 0) {
		close(devfd);
		return;
	}
	// The mode must come from mknodat alone, without a second lookup by name.
	umask(0);
	if (mknodat(devfd, "fuse", S_IFCHR | 0666,
		    makedev(FUSE_DEV_MAJOR, FUSE_DEV_MINOR)) < 0) {
		die_errno("create /dev/fuse in target namespace");
	}
	close(devfd);
}

static void do_mount(int fuse_fd, int target_fd, const char *target,
		     unsigned long flags, const char *opts, const char *type,
		     const char *source, unsigned long user_id,
		     unsigned long group_id) {
	char data[MOUNT_DATA_MAX];
	struct stat st;
	int tfd = target_fd;
	int n;

	if (tfd < 0) {
		tfd = open(target, O_PATH | O_DIRECTORY | O_CLOEXEC);
		if (tfd < 0) {
			die_errno("open mountpoint in target namespace");
		}
	}
	if (fstat(tfd, &st) < 0) {
		die_errno("stat mountpoint");
	}
	if (!S_ISDIR(st.st_mode)) {
		die("mountpoint is not a directory");
	}
	// Mount on "." so the directory pinned by tfd is the target.
	if (fchdir(tfd) < 0) {
		die_errno("fchdir to mountpoint");
	}

	n = snprintf(data, sizeof(data),
		     "fd=%d,rootmode=%o,user_id=%lu,group_id=%lu%s%s", fuse_fd,
		     (unsigned int)(st.st_mode & S_IFMT), user_id, group_id,
		     opts[0] != '\0' ? "," : "", opts);
	if (n < 0 || (size_t)n >= sizeof(data)) {
		die("mount options too long");
	}

	if (mount(source, ".", type, flags, data) == 0) {
		return;
	}
	// Kernels without subtype support take it as "<subtype>#<source>".
	if (errno == ENODEV && strncmp(type, "fuse.", 5) == 0) {
		char alt[PATH_MAX];

		n = snprintf(alt, sizeof(alt), "%s#%s", type + 5, source);
		if (n > 0 && (size_t)n < sizeof(alt) &&
		    mount(alt, ".", "fuse", flags, data) == 0) {
			return;
		}
		errno = ENODEV;
	}
	die_errno("mount");
}

// unescape_mountinfo rewrites the octal escapes that /proc/*/mountinfo uses for
// space, tab, newline and backslash. It rewrites in place; the result is never
// longer than the input.
static void unescape_mountinfo(char *s) {
	char *w = s;

	for (const char *r = s; *r != '\0'; r++) {
		if (r[0] == '\\' && r[1] >= '0' && r[1] <= '3' &&
		    r[2] >= '0' && r[2] <= '7' && r[3] >= '0' && r[3] <= '7') {
			*w++ = (char)(((r[1] - '0') << 6) |
				      ((r[2] - '0') << 3) | (r[3] - '0'));
			r += 3;
			continue;
		}
		*w++ = *r;
	}
	*w = '\0';
}

// mountinfo_fstype finds the filesystem type mounted at path. Returns -1 when
// path is not a mountpoint. It reads mountinfo instead of calling statfs(2),
// which blocks on a FUSE mount whose daemon has died.
static int mountinfo_fstype(int proc_self_fd, const char *path, char *fstype,
			    size_t fstype_len) {
	char line[MOUNTINFO_LINE_MAX];
	FILE *f;
	int fd;
	int found = -1;

	fd = openat(proc_self_fd, "mountinfo", O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		die_errno("open mountinfo for target namespace");
	}
	f = fdopen(fd, "r");
	if (f == NULL) {
		die_errno("read mountinfo for target namespace");
	}

	while (fgets(line, sizeof(line), f) != NULL) {
		char *saveptr = NULL;
		char *mountpoint;
		char *tok;
		int field = 0;

		// Fields: id parent major:minor root mountpoint opts
		// [optional...] - fstype source superopts
		tok = strtok_r(line, " \n", &saveptr);
		while (tok != NULL && field < 4) {
			tok = strtok_r(NULL, " \n", &saveptr);
			field++;
		}
		if (tok == NULL) {
			continue;
		}
		mountpoint = tok;
		unescape_mountinfo(mountpoint);
		if (strcmp(mountpoint, path) != 0) {
			continue;
		}
		// Skip the variable-length optional fields up to the separator.
		while ((tok = strtok_r(NULL, " \n", &saveptr)) != NULL) {
			if (strcmp(tok, "-") == 0) {
				break;
			}
		}
		if (tok == NULL) {
			continue;
		}
		tok = strtok_r(NULL, " \n", &saveptr);
		if (tok == NULL) {
			continue;
		}
		// Later lines win: a stacked mount hides the ones beneath it,
		// and umount2 removes the topmost.
		snprintf(fstype, fstype_len, "%s", tok);
		found = 0;
	}
	fclose(f);
	return found;
}

// resolve_mountpoint canonicalises target by resolving only its parent, so the
// mountpoint inode, possibly a dead FUSE mount, is never touched.
static void resolve_mountpoint(const char *target, char *out, size_t out_len) {
	char buf[PATH_MAX];
	char parent_real[PATH_MAX];
	const char *base;
	char *slash;
	int n;

	if (strlen(target) >= sizeof(buf)) {
		die("mountpoint path too long");
	}
	snprintf(buf, sizeof(buf), "%s", target);
	// Trailing slashes would hide the final component from the split.
	for (size_t i = strlen(buf); i > 1 && buf[i - 1] == '/'; i--) {
		buf[i - 1] = '\0';
	}
	if (strcmp(buf, "/") == 0) {
		die("refusing to unmount the namespace root");
	}

	slash = strrchr(buf, '/');
	if (slash == NULL) {
		base = buf;
		if (realpath(".", parent_real) == NULL) {
			die_errno("resolve mountpoint parent");
		}
	} else {
		base = slash + 1;
		if (slash == buf) {
			snprintf(parent_real, sizeof(parent_real), "/");
		} else {
			*slash = '\0';
			if (realpath(buf, parent_real) == NULL) {
				die_errno("resolve mountpoint parent");
			}
		}
	}
	if (base[0] == '\0' || strcmp(base, ".") == 0 ||
	    strcmp(base, "..") == 0) {
		die("invalid mountpoint path");
	}

	n = snprintf(out, out_len, "%s%s%s", parent_real,
		     strcmp(parent_real, "/") == 0 ? "" : "/", base);
	if (n < 0 || (size_t)n >= out_len) {
		die("mountpoint path too long");
	}
}

static void do_umount(int proc_self_fd, const char *target, int lazy) {
	char canonical[PATH_MAX];
	char fstype[64];

	resolve_mountpoint(target, canonical, sizeof(canonical));

	if (mountinfo_fstype(proc_self_fd, canonical, fstype, sizeof(fstype)) <
	    0) {
		fprintf(stderr, "%s: %s is not a mountpoint\n", progname,
			canonical);
		exit(1);
	}
	// Only FUSE mounts may be unmounted.
	if (strcmp(fstype, "fuse") != 0 && strncmp(fstype, "fuse.", 5) != 0 &&
	    strcmp(fstype, "fuseblk") != 0) {
		fprintf(stderr,
			"%s: refusing to unmount %s: filesystem type is %s, "
			"not fuse\n",
			progname, canonical, fstype);
		exit(1);
	}
	if (umount2(canonical, UMOUNT_NOFOLLOW | (lazy ? MNT_DETACH : 0)) < 0) {
		die_errno("umount");
	}
}

int main(int argc, char **argv) {
	const char *op;
	const char *target = NULL;
	const char *opts = "";
	const char *type = "fuse";
	const char *source = "/dev/fuse";
	unsigned long flags = 0;
	unsigned long user_id = 0;
	unsigned long group_id = 0;
	int ns_fd = -1;
	int fuse_fd = -1;
	int target_fd = -1;
	int ensure_dev = 0;
	int lazy = 0;
	int proc_self_fd = -1;

	if (argc < 2) {
		usage();
	}
	op = argv[1];

	for (int i = 2; i < argc; i++) {
		const char *v;

		if ((v = opt_value(argv[i], "ns-fd")) != NULL) {
			ns_fd = parse_fd(v, "--ns-fd");
		} else if ((v = opt_value(argv[i], "fuse-fd")) != NULL) {
			fuse_fd = parse_fd(v, "--fuse-fd");
		} else if ((v = opt_value(argv[i], "target-fd")) != NULL) {
			target_fd = parse_fd(v, "--target-fd");
		} else if ((v = opt_value(argv[i], "target")) != NULL) {
			target = v;
		} else if ((v = opt_value(argv[i], "opts")) != NULL) {
			opts = v;
		} else if ((v = opt_value(argv[i], "type")) != NULL) {
			type = v;
		} else if ((v = opt_value(argv[i], "source")) != NULL) {
			source = v;
		} else if ((v = opt_value(argv[i], "flags")) != NULL) {
			flags = parse_ulong(v, "--flags");
		} else if ((v = opt_value(argv[i], "user-id")) != NULL) {
			user_id = parse_ulong(v, "--user-id");
		} else if ((v = opt_value(argv[i], "group-id")) != NULL) {
			group_id = parse_ulong(v, "--group-id");
		} else if (strcmp(argv[i], "--ensure-dev") == 0) {
			ensure_dev = 1;
		} else if (strcmp(argv[i], "--lazy") == 0) {
			lazy = 1;
		} else {
			fprintf(stderr, "%s: unknown argument: %s\n", progname,
				argv[i]);
			usage();
		}
	}

	if (ns_fd < 0) {
		die("--ns-fd is required");
	}

	if (strcmp(op, "umount") == 0) {
		if (target == NULL) {
			die("--target is required for umount");
		}
		// Must precede setns: /proc/self does not resolve in the caller's
		// /proc, and mountinfo opened later still lists the new namespace.
		proc_self_fd = open("/proc/self", O_PATH | O_DIRECTORY | O_CLOEXEC);
		if (proc_self_fd < 0) {
			die_errno("open /proc/self");
		}
		enter_mount_namespace(ns_fd);
		do_umount(proc_self_fd, target, lazy);
		return 0;
	}

	if (strcmp(op, "mount") != 0) {
		fprintf(stderr, "%s: unknown operation: %s\n", progname, op);
		usage();
	}
	if (fuse_fd < 0) {
		die("--fuse-fd is required for mount");
	}
	if (target_fd < 0 && target == NULL) {
		die("--target-fd or --target is required for mount");
	}

	enter_mount_namespace(ns_fd);
	if (ensure_dev) {
		ensure_fuse_device();
	}
	do_mount(fuse_fd, target_fd, target, flags, opts, type, source,
		 user_id, group_id);
	return 0;
}
