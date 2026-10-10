/*
 * SPDX-FileCopyrightText: Copyright (c) 2015 Grzegorz Kostka (kostka.grzegorz@gmail.com)
 * SPDX-License-Identifier: BSD-3-Clause
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * - Redistributions of source code must retain the above copyright
 *   notice, this list of conditions and the following disclaimer.
 * - Redistributions in binary form must reproduce the above copyright
 *   notice, this list of conditions and the following disclaimer in the
 *   documentation and/or other materials provided with the distribution.
 * - The name of the author may not be used to endorse or promote products
 *   derived from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
 * IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
 * OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 * IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
 * NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
 * THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 *
 * Modifications:
 * SPDX-FileCopyrightText: 2023 Richard Masters <grick23@gmail.com>
 * SPDX-License-Identifier: MIT
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdbool.h>
#include <inttypes.h>
#include <time.h>
#include <unistd.h>
#include <sys/time.h>

#include <ext4.h>
#include <ext4_mkfs.h>
#include "../blockdev/linux/file_dev.h"
#include "../blockdev/windows/file_windows.h"

#include "linux24-gzip.h"

#define BLOCK_SIZE 1024
#define FILENAME_LENGTH 1024

/*
 * The builder keeps source files above 0x54000000 while compiling. Once the
 * build has reached this helper, most process snapshots have been released.
 * Compact the live file pool into that reclaimed low memory before reserving
 * the Linux image. The image then fits below the T440p's last usable low-RAM
 * address rather than being appended above 2 GiB.
 */
#define BUILDER_FILE_TABLE 0x01000000u
#define BUILDER_FILE_NAMES 0x00fffc00u
#define BUILDER_FILE_POOL 0x54000000u
#define COMPACTED_FILE_POOL 0x15000000u
#define SAVED_PROCESS_POOL 0x30000000u
#define BUILDER_NEXT_FILE 0x00008307u
#define BUILDER_NEXT_SAVE 0x000085ffu
#define INITRD_LOW_MEMORY_END 0x7f617000u
#define INITRD_SAVE_GUARD 0x04000000u
#define INITRD_ALIGNMENT 0x01000000u
#define HANDOFF_POOL_GAP 0x01000000u

const char *input_name = NULL;
/**@brief   Block device handle.*/
static struct ext4_blockdev *bd;
/**@brief   Block cache handle.*/
static struct ext4_bcache *bc;
static struct ext4_fs fs;
static unsigned int compacted_file_end;

static unsigned int align_up(unsigned int value, unsigned int alignment)
{
	return (value + alignment - 1) & ~(alignment - 1);
}

static bool compact_builder_file_pool(unsigned int initrd_size,
				      unsigned int *initrd_address)
{
	unsigned int old_end;
	unsigned int pool_size;
	unsigned int compacted_end;
	unsigned int saved_end;
	unsigned int image_end;
	unsigned int process_num;
	unsigned int process_desc;
	unsigned int active_brk;
	int filenum;
	unsigned int filename_addr;
	unsigned int file_addr;
	volatile unsigned int *next_file;
	volatile unsigned int *next_save;

	next_file = (volatile unsigned int *) BUILDER_NEXT_FILE;
	next_save = (volatile unsigned int *) BUILDER_NEXT_SAVE;
	old_end = *next_file;
	if (old_end < BUILDER_FILE_POOL) {
		puts("Linux initrd error: file pool ends below its configured base");
		return false;
	}
	/* Compact live files only: the append-only pool also contains dead tarballs. */
	compacted_end = COMPACTED_FILE_POOL;
	process_num = *((unsigned int *) 0x0000866d);
	if (process_num < 2 || process_num > 16) return false;
	process_desc = 0x00020000u + (process_num - 1) * 0x1000u;
	active_brk = *((unsigned int *) (process_desc + 4));
	if (active_brk > COMPACTED_FILE_POOL - 0x04000000u) {
		puts("Linux initrd error: active process overlaps compacted pool");
		return false;
	}
	/* The builder does not implement unlink: rm leaves the expanded input tar
	 * in its append-only table. It is no longer needed after kernel linking. */
	for (filenum = 14335, filename_addr = BUILDER_FILE_NAMES,
	     file_addr = BUILDER_FILE_TABLE + 0x37ff4;
	     filenum >= 3;
	     filenum--, filename_addr -= FILENAME_LENGTH, file_addr -= 16) {
		if (!strcmp((char *)filename_addr,
		    "/steps/linux-2.4.26-lb1/build/linux-2.4.26.tar")) {
			((char *)filename_addr)[0] = 0;
			*((unsigned int *)(file_addr + 4)) = 0;
		}
	}
	/* Validate all ranges and the total before moving bytes. */
	for (filenum = 14335, filename_addr = BUILDER_FILE_NAMES,
	     file_addr = BUILDER_FILE_TABLE + 0x37ff4;
	     filenum >= 3;
	     filenum--, filename_addr -= FILENAME_LENGTH, file_addr -= 16) {
		unsigned int address = *((unsigned int *)file_addr);
		unsigned int length = *((unsigned int *)(file_addr + 4));
		if (((char *)filename_addr)[0] != '/' || !length) continue;
		if ((address < BUILDER_FILE_POOL && strcmp((char *)filename_addr, "/e820")) || address >= old_end ||
		    length > old_end - address || length > SAVED_PROCESS_POOL - compacted_end) {
			puts("Linux initrd error: live file pool exceeds reclaimed space");
			return false;
		}
		compacted_end += length;
	}
	compacted_file_end = compacted_end;
	compacted_end = COMPACTED_FILE_POOL;
	for (filenum = 14335, filename_addr = BUILDER_FILE_NAMES,
	     file_addr = BUILDER_FILE_TABLE + 0x37ff4;
	     filenum >= 3;
	     filenum--, filename_addr -= FILENAME_LENGTH, file_addr -= 16) {
		unsigned int address = *((unsigned int *)file_addr);
		unsigned int length = *((unsigned int *)(file_addr + 4));
		if (((char *)filename_addr)[0] != '/' || !length) continue;
		memcpy((void *)compacted_end,(void *)address,length);
		*((unsigned int *)file_addr) = compacted_end;
		compacted_end += length;
	}
	pool_size = compacted_end - COMPACTED_FILE_POOL;
	saved_end = *next_save;
	if (saved_end > 0xffffffffu - INITRD_SAVE_GUARD - INITRD_ALIGNMENT) {
		puts("Linux initrd error: saved-process range overflows");
		return false;
	}
	*initrd_address = align_up(saved_end + INITRD_SAVE_GUARD,
				   INITRD_ALIGNMENT);
	if (*initrd_address >= INITRD_LOW_MEMORY_END) {
		puts("Linux initrd error: no T440p low RAM remains for image");
		return false;
	}
	if (initrd_size > (INITRD_LOW_MEMORY_END - *initrd_address) / BLOCK_SIZE) {
		puts("Linux initrd error: requested image exceeds T440p low RAM");
		return false;
	}
	image_end = *initrd_address + initrd_size * BLOCK_SIZE;
	if (image_end > INITRD_LOW_MEMORY_END) {
		puts("Linux initrd error: image end exceeds T440p low RAM");
		return false;
	}

	*next_file = *initrd_address;
	printf("Compacted source pool: 0x%08x-0x%08x (%u bytes)\n",
	       COMPACTED_FILE_POOL, compacted_end, pool_size);
	printf("Reserved Linux image: 0x%08x-0x%08x (%u bytes)\n",
	       *initrd_address, image_end, initrd_size * BLOCK_SIZE);
	return true;
}

/* Stage2 clears the active-process window before loading the next command.
 * Keep only the files needed by the immediate Linux handoff above that window
 * and beyond the initrd, then rebase those file-table entries. */
static bool preserve_linux24_handoff_files(unsigned int initrd_address,
					 unsigned int initrd_size)
{
	static const char *required[] = {
		"/usr/bin/match",
		"/usr/bin/kexec-linux24",
		"/boot/vmlinux-linux24",
		"/boot/linux24.ext2",
		"/e820",
	};
	unsigned int image_end;
	unsigned int handoff_address;
	unsigned int handoff_end;
	unsigned int filename_addr;
	unsigned int file_addr;
	volatile unsigned int *next_file;
	unsigned int i;

	image_end = initrd_address + initrd_size * BLOCK_SIZE;
	if (image_end > 0xffffffffu - HANDOFF_POOL_GAP - INITRD_ALIGNMENT) {
		puts("Linux handoff error: image end overflows address space");
		return false;
	}
	handoff_address = align_up(image_end + HANDOFF_POOL_GAP,
				   INITRD_ALIGNMENT);
	if (handoff_address >= INITRD_LOW_MEMORY_END) {
		puts("Linux handoff error: no low RAM remains after initrd");
		return false;
	}
	handoff_end = handoff_address;

	for (i = 0; i < sizeof(required) / sizeof(required[0]); i++) {
		int filenum;
		bool found = false;

		for (filenum = 14335, filename_addr = BUILDER_FILE_NAMES,
			 file_addr = BUILDER_FILE_TABLE + 0x37ff4;
		     filenum >= 3;
		     filenum--, filename_addr -= FILENAME_LENGTH, file_addr -= 16) {
			unsigned int address;
			unsigned int length;

			if (strcmp((char *) filename_addr, required[i]))
				continue;
			found = true;
			address = *((unsigned int *) file_addr);
			length = *((unsigned int *) (file_addr + 4));
			if (address >= COMPACTED_FILE_POOL &&
			    address < compacted_file_end) {
				if (length > compacted_file_end - address) {
					puts("Linux handoff error: file exceeds compacted pool");
					return false;
				}
			} else if (!strcmp(required[i], "/boot/linux24.ext2") &&
				   address == initrd_address &&
				   length <= initrd_size * BLOCK_SIZE) {
				/* The helper created this file directly at the reserved image. */
			} else {
				if (!strcmp(required[i], "/boot/linux24.ext2") ||
				    address > INITRD_LOW_MEMORY_END ||
				    length > INITRD_LOW_MEMORY_END - address ||
				    (address < image_end &&
				     address + length > initrd_address)) {
					printf("Linux handoff error: invalid %s at %08x len %08x\n",
					       required[i], address, length);
					return false;
				}
			}
			if (strcmp(required[i], "/boot/linux24.ext2") ||
			    address != initrd_address) {
				if (handoff_end > INITRD_LOW_MEMORY_END ||
				    length > INITRD_LOW_MEMORY_END - handoff_end) {
					puts("Linux handoff error: handoff files exceed low RAM");
					return false;
				}
				memcpy((void *) handoff_end, (const void *) address, length);
				*((unsigned int *) file_addr) = handoff_end;
				handoff_end = align_up(handoff_end + length, 16);
			}
			break;
		}
		if (!found) {
			printf("Linux handoff error: missing %s\n", required[i]);
			return false;
		}
	}
	if (handoff_end > INITRD_LOW_MEMORY_END) {
		puts("Linux handoff error: handoff pool exceeds low RAM");
		return false;
	}
	next_file = (volatile unsigned int *) BUILDER_NEXT_FILE;
	*next_file = handoff_end;
	printf("Preserved Linux handoff files: 0x%08x-0x%08x\n",
	       handoff_address, handoff_end);
	return true;
}


static struct ext4_mkfs_info info = {
	.block_size = BLOCK_SIZE,
	.journal = false,
	.inode_size = 128,
};

#define MKDEV(major, minor) (((major) << 8) | (minor))

static bool open_filedev(void)
{
	file_dev_name_set(input_name);
	bd = file_dev_get();
	if (!bd) {
		puts("open_filedev: fail");
		return false;
	}
	return true;
}

bool lwext4_mount(struct ext4_blockdev *bdev, struct ext4_bcache *bcache)
{
	int r;

	bc = bcache;
	bd = bdev;

	if (!bd) {
		puts("lwext4_mount: no block device");
		return false;
	}

	/* ext4_dmask_set(DEBUG_ALL); */

	r = ext4_device_register(bd, "ext4_fs");
	if (r != EOK) {
		printf("ext4_device_register: rc = %d\n", r);
		return false;
	}

	r = ext4_mount("ext4_fs", "/mp/", false);
	if (r != EOK) {
		printf("ext4_mount: rc = %d\n", r);
		return false;
	}

	r = ext4_recover("/mp/");
	if (r != EOK && r != ENOTSUP) {
		printf("ext4_recover: rc = %d\n", r);
		return false;
	}

	/* ext4_cache_write_back("/mp/", 1); */

	return true;
}

bool lwext4_umount(void)
{
	int r;

	/* ext4_cache_write_back("/mp/", 0); */

	r = ext4_umount("/mp/");
	if (r != EOK) {
		printf("ext4_umount: fail %d", r);
		return false;
	}
	return true;
}


bool copy_file(char *src_path, char *dest_path)
{
	ext4_file dest_file;
	FILE *src_file;
	char buffer[16384];
	int err;
	int read_len;
	int read_failed;

	err = ext4_inode_exist(dest_path, EXT4_DE_UNKNOWN);
	if (err == EOK) {
		/* file already copied or directory already created */
		return EXIT_SUCCESS;
	}

	printf("copy_file: %s\n", src_path);

	src_file = fopen(src_path, "rb");
	if (!src_file) {
		printf("fopen '%s' error.\n", src_path);
		return EXIT_FAILURE;
	}

	err = ext4_fopen(&dest_file, dest_path, "wb");
	if (err != EOK) {
		printf("ext4_open error: %d\n", err);
		fclose(src_file);
		return EXIT_FAILURE;
	}

	read_failed = 0;
	while (1) {
		/* Keep the helper's heap below the relocated live source pool. */
		read_len = fread(buffer, 1, sizeof(buffer), src_file);
		if (read_len == 0)
			break;
		err = ext4_fwrite(&dest_file, buffer, read_len, 0);
		if (err != EOK) {
			printf("ext4_fwrite error: %d\n", err);
			read_failed = 1;
			break;
		}
	}
	fclose(src_file);
	if (read_failed)
		return EXIT_FAILURE;

	err = ext4_fclose(&dest_file);
	if (err != EOK) {
		printf("ext4_fclose error: %d\n", err);
		return EXIT_FAILURE;
	}

	return EXIT_SUCCESS;
}

bool copy_file_system()
{
	int filenum;
	unsigned int filename_addr;
	/* +4 to account for /mp and null termination */
	char dst_filename[FILENAME_LENGTH + 4];
	for (filenum = 14335, filename_addr = 0xfffc00; filenum >= 3; filenum--, filename_addr -= FILENAME_LENGTH) {
		/* Avoid including linux24.ext2 in itself */
		if (((char *) filename_addr)[0] != '/' ||
			!strcmp((char *) filename_addr, "/") ||
			!strcmp((char *) filename_addr, "/boot/linux24.ext2")) {
			continue;
		}
		strcpy(dst_filename, "/mp");
		strcat(dst_filename, (char *) filename_addr);
		if (copy_file((char *)filename_addr, dst_filename) != EXIT_SUCCESS)
			return false;
	}
	return true;
}

int main(int argc, char **argv)
{
	int err;

	char zeros[BLOCK_SIZE];

	unsigned int next_file_address;
	unsigned int initrd_size;
	int i;

	input_name = "/boot/linux24.ext2";
	initrd_size = 262144;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-s")) {
			i++;
			initrd_size = atoi(argv[i]);
		} else {
			input_name = argv[i];
		}
	}
	if (!compact_builder_file_pool(initrd_size, &next_file_address))
		return EXIT_FAILURE;

	printf("Starting %s at addr 0x%08x\n", input_name, next_file_address);

	memset(zeros, 0, BLOCK_SIZE);
	FILE *ext2file = fopen(input_name, "w");
	int b;
	for (b=0; b < initrd_size; b++)
		fwrite(zeros, BLOCK_SIZE, 1, ext2file);
	fclose(ext2file);

	if (!open_filedev()) {
		printf("open_filedev error\n");
		return EXIT_FAILURE;
	}

	/* ext4_dmask_set(DEBUG_ALL); */

	err = ext4_mkfs(&fs, bd, &info, F_SET_EXT2_V0);
	if (err != EOK) {
		printf("ext4_mkfs error: %d\n", err);
		return EXIT_FAILURE;
	}

	memset(&info, 0, sizeof(struct ext4_mkfs_info));
	err = ext4_mkfs_read_info(bd, &info);
	if (err != EOK) {
		printf("ext4_mkfs_read_info error: %d\n", err);
		return EXIT_FAILURE;
	}

	printf("Created filesystem with parameters:\n");
	printf("Size: %"PRIu64"\n", info.len);
	printf("Block size: %"PRIu32"\n", info.block_size);
	printf("Blocks per group: %"PRIu32"\n", info.blocks_per_group);
	printf("Inodes per group: %"PRIu32"\n",	info.inodes_per_group);
	printf("Inode size: %"PRIu32"\n", info.inode_size);
	printf("Inodes: %"PRIu32"\n", info.inodes);
	printf("Journal blocks: %"PRIu32"\n", info.journal_blocks);
	printf("Features ro_compat: 0x%x\n", info.feat_ro_compat);
	printf("Features compat: 0x%x\n", info.feat_compat);
	printf("Features incompat: 0x%x\n", info.feat_incompat);
	printf("BG desc reserve: %"PRIu32"\n", info.bg_desc_reserve_blocks);
	printf("Descriptor size: %"PRIu32"\n",info.dsc_size);
	printf("Label: %s\n", info.label);

	if (!lwext4_mount(bd, bc))
		return EXIT_FAILURE;

	puts("ext4_dir_mk /mp/dev");
	err = ext4_dir_mk("/mp/dev");
	if (err != EOK) {
		printf("ext4_dir_mk error: %d\n", err);
	}
	puts("ext4_dir_mk /mp/tmp");
	err = ext4_dir_mk("/mp/tmp");
	if (err != EOK) {
		printf("ext4_dir_mk error: %d\n", err);
	}

	puts("ext4_mknod /mp/dev/console");
	err = ext4_mknod("/mp/dev/console", EXT4_DE_CHRDEV, MKDEV(5, 1));
	if (err != EOK) {
		printf("ext4_mknod error: %d\n", err);
		return EXIT_FAILURE;
	}
	puts("ext4_mknod /mp/dev/ram0");
	err = ext4_mknod("/mp/dev/ram0", EXT4_DE_BLKDEV, MKDEV(1, 0));
	if (err != EOK) {
		printf("ext4_mknod error: %d\n", err);
		return EXIT_FAILURE;
	}
	puts("ext4_mknod /mp/dev/ram1");
	err = ext4_mknod("/mp/dev/ram1", EXT4_DE_BLKDEV, MKDEV(1, 1));
	if (err != EOK) {
		printf("ext4_mknod error: %d\n", err);
		return EXIT_FAILURE;
	}

	if (!copy_file_system()) return EXIT_FAILURE;

	if (!lwext4_umount())
		return EXIT_FAILURE;
	if (!preserve_linux24_handoff_files(next_file_address, initrd_size))
		return EXIT_FAILURE;

    /* The ext2 driver expects low-memory RAM-disk backing pages. Compress
     * the boot image so its input and the 1GiB expanded disk fit together.
     * All files needed after this helper have already been preserved above
     * the raw image, so this temporary output may reclaim the source pool. */
    {
        volatile unsigned int *next_file = (volatile unsigned int *)BUILDER_NEXT_FILE;
        unsigned int preserved_end = *next_file;
        unsigned int process_num = *((unsigned int *)0x0000866d);
        unsigned int active_brk = *((unsigned int *)(0x00020000u +
                                      (process_num - 1) * 0x1000u + 4));
        unsigned int temporary = align_up(active_brk + INITRD_SAVE_GUARD,
                                          INITRD_ALIGNMENT);
        unsigned int compressed;
        FILE *image;
        if (temporary < 0x10000000u) temporary = 0x10000000u;
        if (temporary >= SAVED_PROCESS_POOL) return EXIT_FAILURE;
        compressed = gzip_image((unsigned char *)next_file_address,
                                initrd_size * BLOCK_SIZE,
                                (unsigned char *)temporary,
                                SAVED_PROCESS_POOL - temporary);
        if (!compressed) {
            puts("Linux initrd error: compressed image exceeds temporary pool");
            return EXIT_FAILURE;
        }
        *next_file = next_file_address;
        image = fopen("/boot/linux24.ext2.gz", "wb");
        if (!image) return EXIT_FAILURE;
        if (fwrite((void *)temporary, 1, compressed, image) != compressed) {
            fclose(image); return EXIT_FAILURE;
        }
        if (fclose(image)) return EXIT_FAILURE;
        *next_file = preserved_end;
        printf("Compressed Linux initrd: %u bytes at 0x%08x\n",
               compressed, next_file_address);
    }
	puts("Linux ext2 initrd created successfully.");
	return EXIT_SUCCESS;
}
