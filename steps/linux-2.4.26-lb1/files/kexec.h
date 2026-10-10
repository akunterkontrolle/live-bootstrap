/* Derived from Eric W. Biederman's 2002 kexec headers.
 * SPDX-License-Identifier: GPL-2.0-only
 */
#ifndef LINUX_KEXEC_H
#define LINUX_KEXEC_H

#if CONFIG_KEXEC
#include <linux/types.h>


/* 
 * This structure is used to hold the arguments that are used when loading
 * kernel binaries.
 */

typedef unsigned long kimage_entry_t;
#define IND_DESTINATION  0x1
#define IND_INDIRECTION  0x2
#define IND_DONE         0x4
#define IND_SOURCE       0x8

struct kexec_segment;
struct kimage {
	kimage_entry_t head;
	kimage_entry_t *entry;
	kimage_entry_t *last_entry;


	unsigned long start;
	void *reboot_code_buffer;
 unsigned long rejected;
 struct kexec_segment *segments;
 int nr_segments;
};

/* kexec helper functions */
void kimage_init(struct kimage *image);
void kimage_free(struct kimage *image);

struct kexec_segment {
	void *buf;
	size_t bufsz;
	void *mem;
	size_t memsz;
};

struct kexec_args {
 unsigned long entry;
 long nr_segments;
 struct kexec_segment *segments;
};

/* kexec interface functions */
extern void machine_kexec(struct kimage *image);
extern int do_kexec(unsigned long entry, long nr_segments, 
	struct kexec_segment *segments, struct kimage *image);
#endif
#endif /* LINUX_KEXEC_H */

