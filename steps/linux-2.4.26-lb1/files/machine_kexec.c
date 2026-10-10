/* Derived from Eric W. Biederman's 2002 i386 kexec implementation.
 * SPDX-License-Identifier: GPL-2.0-only
 */
#include <linux/config.h>
#include <linux/mm.h>
#include <linux/kexec.h>
#include <asm/pgtable.h>
#include <asm/pgalloc.h>
#include <asm/io.h>

static int identity_map_page(unsigned long address)
{
 struct mm_struct *mm=current->mm;
 pgd_t *pgd; pmd_t *pmd; pte_t *pte; int result=-ENOMEM;
 spin_lock(&mm->page_table_lock);
 pgd=pgd_offset(mm,address);pmd=pmd_alloc(mm,pgd,address);
 if(pmd){
  pte=pte_alloc(mm,pmd,address);
  if(pte){set_pte(pte,mk_pte(virt_to_page(phys_to_virt(address)),PAGE_KERNEL));__flush_tlb_one(address);result=0;}
 }
 spin_unlock(&mm->page_table_lock);return result;
}
static void load_segments(void)
{
 __asm__ __volatile__("ljmp $0x10,$1f\n1:\nmovl $0x18,%%eax\nmovl %%eax,%%ds\nmovl %%eax,%%es\nmovl %%eax,%%fs\nmovl %%eax,%%gs\nmovl %%eax,%%ss":::"eax","memory");
}
extern unsigned char relocate_new_kernel[];
extern unsigned int relocate_new_kernel_size;
void machine_kexec(struct kimage *image)
{
 unsigned long buffer=virt_to_phys(image->reboot_code_buffer);
 unsigned long head=image->head&PAGE_MASK;
 unsigned char descriptor[6]={0,0,0,0,0,0};
 void (*relocate)(unsigned long,unsigned long,unsigned long)=(void*)buffer;
 /* Allocate page tables while normal interrupts and scheduling still work. */
 if(identity_map_page(buffer))panic("kexec: cannot identity-map trampoline");
 memcpy(image->reboot_code_buffer,relocate_new_kernel,relocate_new_kernel_size);
 local_irq_disable();outb(255,0x21);outb(255,0xa1);
 load_segments();
 __asm__ __volatile__("lgdt %0\nlidt %0"::"m"(descriptor):"memory");
 relocate(head,buffer,image->start);
 panic("kexec returned");
}
