/*
  FUSE: Filesystem in Userspace
  Copyright (C) 2001-2007  Miklos Szeredi <miklos@szeredi.hu>
  Copyright (C) 2011       Sebastian Pipping <sebastian@pipping.org>

  This program can be distributed under the terms of the GNU GPLv2.
  See the file COPYING.
*/

/** @file
 *
 * This file system redirects the write and read on mountpoint to other
 * designated directories to work along with sync daemon and cloud storage
 * mount under sky/data/csync/sky_csync.py. The code is inspired by an example
 * provided at:
 * 		https://github.com/libfuse/libfuse/blob/master/example/passthrough.c		
 * 
 * Compile with
 *
 *     gcc -Wall -D_FILE_OFFSET_BITS=64 redirect-fuse.c set.c `pkg-config fuse3 --cflags --libs` -o redirect-fuse
 *
 * ## Source code ##
 * \include redirect-fuse.c
 */


#define FUSE_USE_VERSION 31

#define _GNU_SOURCE

#ifdef linux
/* For pread()/pwrite()/utimensat() */
#define _XOPEN_SOURCE 700
#endif

#include <fuse.h>
#include <libgen.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <dirent.h>
#include <errno.h>
#include <stdlib.h>
#ifdef __FreeBSD__
#include <sys/socket.h>
#include <sys/un.h>
#endif
#include <sys/time.h>

#include "set.h"

static int use_detailed_listing = 0;
static char* mountpoint_path = NULL;
static char* csync_read_path = NULL;
static char* csync_write_path = NULL;


char* join_path(const char* base_path, const char* partial_path) {
    if (base_path == NULL || partial_path == NULL) {
        return NULL;
    }

    // Allocate enough space for the full path
	// +2 for '/' and '\0'
    char* full_path = malloc(strlen(base_path) + strlen(partial_path) + 2);
    if (full_path == NULL) {
        return NULL; // Allocation failed
    }

    strcpy(full_path, base_path);
    strcat(full_path, "/");
    strcat(full_path, partial_path);

    return full_path;
}

int file_exists(const char* path) {
    struct stat buffer;
    return (stat(path, &buffer) == 0); 
}

char* csync_full_path(const char* partial_path, int write, int read) {
	// Check and remove leading '/' from partial_path if present
	if (partial_path[0] == '/') {
		// Move the pointer to skip the leading '/'
		partial_path++;
	}

	char* result = NULL;

	if (write) {
		result = join_path(csync_write_path, partial_path);
	} else if (read) {
		result = join_path(csync_read_path, partial_path);
	} else {
		char* full_write_path = join_path(csync_write_path, partial_path);
		if (file_exists(full_write_path)) {
			result = full_write_path;
		} else {
			free(full_write_path);
			result = join_path(csync_read_path, partial_path);
		}
	}

	return result;
}

static void* xmp_init(struct fuse_conn_info* conn,
		      struct fuse_config* cfg)
{
	(void) conn;
	cfg->use_ino = 1;

	/* Pick up changes from lower filesystem right away. This is
		also necessary for better hardlink support. When the kernel
		calls the unlink() handler, it does not know the inode of
		the to-be-removed entry and can therefore not invalidate
		the cache of the associated inode - resulting in an
		incorrect st_nlink value being reported for any remaining
		hardlinks to this inode. */
	cfg->entry_timeout = 0;
	cfg->attr_timeout = 0;
	cfg->negative_timeout = 0;

	return NULL;
}

static int xmp_getattr(const char* path, struct stat* stbuf,
		       struct fuse_file_info* fi)
{
	(void) fi;
	char* translated_path = csync_full_path(path, 0, 0);
	int res = lstat(translated_path, stbuf);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_access(const char* path, int mask)
{
	char* translated_path = csync_full_path(path, 0, 0);
	int res = access(translated_path, mask);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}


static int xmp_readdir(const char* path, void* buf, fuse_fill_dir_t filler, off_t offset, struct fuse_file_info* fi, enum fuse_readdir_flags flags) {
	DIR* dp;
	struct dirent* de;

	char* read_path = csync_full_path(path, 0, 1);
	char* write_path = csync_full_path(path, 1, 0);

	// Clear hash table
	SimpleSet set;
	set_init(&set);
	// Process read_path
	dp = opendir(read_path);
	if (dp != NULL) {
		while ((de = readdir(dp)) != NULL) {
			if (set_contains(&set, de->d_name) == SET_FALSE) {
				// stat structure is used to store information about a file or
				// directory, such as inode number, file type, permissions, and
				// etc.
				struct stat st;
				memset(&st, 0, sizeof(st));
				st.st_ino = de->d_ino;
				// sets the file type and mode field of stat structure
				// Also, shifts the directory entry type left by 12 bits to fit
				// into the mode field format. This field is used to determine
				// the type of the file and its permissions.
				st.st_mode = de->d_type << 12;
				if (filler(buf, de->d_name, &st, 0, use_detailed_listing)) {
					break;
				}
				set_add(&set, de->d_name);
			}
		}
		closedir(dp);
	}

	// Process write_path
	dp = opendir(write_path);
	if (dp != NULL) {
		while ((de = readdir(dp)) != NULL) {
			if (set_contains(&set, de->d_name) == SET_FALSE) {
				struct stat st;
				memset(&st, 0, sizeof(st));
				st.st_ino = de->d_ino;
				st.st_mode = de->d_type << 12;
				if (filler(buf, de->d_name, &st, 0, use_detailed_listing)) {
					break;
				}
				set_add(&set, de->d_name);
			}
		}
		closedir(dp);
	}

	free(read_path);
	free(write_path);
	set_destroy(&set);
	return 0;
}

static int mkdir_p(const char* path, mode_t mode) {
	char* tmp = strdup(path);
	char* parent = dirname(tmp);
	int err = 0;

	if (strcmp(parent, ".") == 0 || strcmp(parent, "/") == 0) {
		free(tmp);
		return 0;
	}

	err = mkdir_p(parent, mode);
	free(tmp);

	if (err != 0 && errno != EEXIST) {
		return -1;
	}

	return mkdir(path, mode);
}

static int xmp_mkdir(const char* path, mode_t mode)
{
	char* write_path = csync_full_path(path, 1, 0);
	int res = mkdir_p(write_path, mode);
	free(write_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_unlink(const char* path)
{
	char* write_path = csync_full_path(path, 1, 0);
	char* read_path = csync_full_path(path, 0, 1);
	int res;
	if (file_exists(read_path)) {
		res = unlink(read_path);
		free(read_path);
		if (res == -1) {
			free(write_path);
			return -errno;
		}
	}

	if (file_exists(write_path)) {
		res = unlink(write_path);
		free(write_path);
		if (res == -1) {
			return -errno;
		}
	}
	return 0;
}

static int xmp_rmdir(const char* path)
{
	char* write_path = csync_full_path(path, 1, 0);
	char* read_path = csync_full_path(path, 0, 1);
	int res;
	if (file_exists(read_path)) {
		res = rmdir(read_path);
	}
	free(read_path);
	if (res == -1) {
		free(write_path);
		return -errno;
	}
	if (file_exists(write_path)) {
		res = rmdir(write_path);
	}
	free(write_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}


static int xmp_rename(const char* from, const char* to, unsigned int flags)
{
	// this operation is called when running:
	// mv /path/file_name/in/mountpoint /path/diff_file_name/in/mountpoint
	char* translated_from = csync_full_path(from, 0, 0);
	char* translated_to = csync_full_path(to, 1, 0);
	if (flags) {
		return -EINVAL;
	}
	int res = rename(translated_from, translated_to);
	free(translated_from);
	free(translated_to);
	if (res == -1) {
		return -errno;
	}
	return 0;
}


static int xmp_chmod(const char* path, mode_t mode,
		     struct fuse_file_info* fi)
{
	(void) fi;
	char* translated_path = csync_full_path(path, 0, 0);
	int res = chmod(translated_path, mode);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_chown(const char* path, uid_t uid, gid_t gid,
		     struct fuse_file_info* fi)
{
	(void) fi;
	char* translated_path = csync_full_path(path, 0, 0);
	int res = lchown(translated_path, uid, gid);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_truncate(const char* path, off_t size,
			struct fuse_file_info* fi)
{
	int res;
	char* translated_path = csync_full_path(path, 0, 0);
	if (fi != NULL) {
		res = ftruncate(fi->fh, size);
	}
	else {
		res = truncate(translated_path, size);
	}
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_utimens(const char* path, const struct timespec ts[2],
		       struct fuse_file_info* fi)
{
	(void) fi;
	int res;
	char* write_path = csync_full_path(path, 1, 0);
	/* don't use utime/utimes since they follow symlinks */
	res = utimensat(0, write_path, ts, AT_SYMLINK_NOFOLLOW);
	if (res == -1) {
		return -errno;
	}
	free(write_path);
	return 0;
}

static int xmp_create(const char* path, mode_t mode,
		      struct fuse_file_info* fi)
{
	char* write_path = csync_full_path(path, 1, 0);
	mode_t mkdir_mode = 0755;  // Octal notation
	char* tmp = strdup(write_path);
	char* parent = dirname(tmp);
	mkdir_p(parent, mkdir_mode);
	int res = open(write_path, fi->flags, mode);

	if (res == -1) {
		return -errno;
	}

	free(tmp);
	free(write_path);
	fi->fh = res;
	fi->parallel_direct_writes = 1;
	return 0;
}

static int xmp_open(const char* path, struct fuse_file_info* fi)
{
	char* translated_path = csync_full_path(path, 0, 0);
	int res = open(translated_path, fi->flags);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	fi->fh = res;
	fi->parallel_direct_writes = 1;
	return 0;
}

static int xmp_read(const char* path, char* buf, size_t size, off_t offset,
		    struct fuse_file_info* fi)
{
	int fd;
	int res;
	char* translated_path = csync_full_path(path, 0, 0);
	if(fi == NULL) {
		fd = open(translated_path, O_RDONLY);
	}
	else {
		fd = fi->fh;
	}
	if (fd == -1) {
		return -errno;
	}
	res = pread(fd, buf, size, offset);
	if (res == -1) {
		res = -errno;
	}
	if(fi == NULL) {
		close(fd);
	}
	free(translated_path);
	return res;
}

static int xmp_write(const char* path, const char* buf, size_t size,
		     off_t offset, struct fuse_file_info* fi)
{
	int fd;
	int res;
	char* write_path = csync_full_path(path, 1, 0);
	(void) fi;
	if(fi == NULL) {
		fd = open(write_path, O_RDONLY);
	}
	else {
		fd = fi->fh;
	}
	if (fd == -1) {
		return -errno;
	}
	res = pwrite(fd, buf, size, offset);
	if (res == -1) {
		res = -errno;
	}
	if(fi == NULL) {
		close(fd);
	}
	free(write_path);
	return res;
}

static int xmp_statfs(const char* path, struct statvfs* stbuf)
{
	char* translated_path = csync_full_path(path, 0, 0);
	int res;
	res = statvfs(translated_path, stbuf);
	free(translated_path);
	if (res == -1) {
		return -errno;
	}
	return 0;
}

static int xmp_release(const char* path, struct fuse_file_info* fi)
{
	(void) path;
	close(fi->fh);
	return 0;
}

static int xmp_fsync(const char* path, int isdatasync,
		     struct fuse_file_info* fi)
{
	(void) path;
	(void) isdatasync;
	(void) fi;
	return 0;
}

static off_t xmp_lseek(const char* path, off_t off, int whence, struct fuse_file_info* fi)
{
	int fd;
	off_t res;
	char* translated_path = csync_full_path(path, 0, 0);
	if (fi == NULL) {
		fd = open(translated_path, O_RDONLY);
	}
	else {
		fd = fi->fh;
	}
	free(translated_path);
	if (fd == -1) {
		return -errno;
	}
	res = lseek(fd, off, whence);
	if (res == -1) {
		res = -errno;
	}
	if (fi == NULL) {
		close(fd);
	}
	return res;
}

static const struct fuse_operations xmp_oper = {
	.init           = xmp_init,
	.getattr	= xmp_getattr,
	.access		= xmp_access,
	.readdir	= xmp_readdir,
	.mkdir		= xmp_mkdir,
	.unlink		= xmp_unlink,
	.rmdir		= xmp_rmdir,
	.rename		= xmp_rename,
	.chmod		= xmp_chmod,
	.chown		= xmp_chown,
	.truncate	= xmp_truncate,
	.utimens	= xmp_utimens,
	.open		= xmp_open,
	.create 	= xmp_create,
	.read		= xmp_read,
	.write		= xmp_write,
	.statfs		= xmp_statfs,
	.release	= xmp_release,
	.fsync		= xmp_fsync,
	.lseek		= xmp_lseek,
};

int main(int argc, char* argv[]) {

    if (argc != 4) {
        fprintf(stderr, "Usage: %s <mountpoint_path> <csync_read_path> <csync_write_path>\n", argv[0]);
        exit(EXIT_FAILURE);
    }
	mountpoint_path = realpath(argv[1], NULL);
	csync_read_path = realpath(argv[2], NULL);
	csync_write_path = realpath(argv[3], NULL);

	// Prepare arguments for fuse_main
	char* fuse_argv[4];
	fuse_argv[0] = argv[0];
	fuse_argv[1] = mountpoint_path;
	fuse_argv[2] = "-o";
	fuse_argv[3] = "allow_other";

	int fuse_argc = 4;

	return fuse_main(fuse_argc, fuse_argv, &xmp_oper, NULL);
}
