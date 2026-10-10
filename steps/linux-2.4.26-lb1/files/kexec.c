/* Derived from Eric W. Biederman's 2002 kexec patch for Linux 2.5.44.
 * SPDX-License-Identifier: GPL-2.0-only
 * Staging pages are allocated outside all destinations, so relocation is
 * linear and never overwrites a source that has not yet been consumed.
 */
#include <linux/mm.h>
#include <linux/slab.h>
#include <linux/bootmem.h>
#include <linux/kexec.h>
#include <asm/uaccess.h>
#include <asm/io.h>
#include <asm/e820.h>

static int usable(unsigned long address,unsigned long size)
{
 int i,found=0;
 for(i=0;i<e820.nr_map;i++) {
  unsigned long long first=e820.map[i].addr,last=first+e820.map[i].size;
  unsigned long long end=(unsigned long long)address+size;
  if(last<first)return 0;
  if(e820.map[i].type==E820_RAM && address>=first && end<=last)found=1;
  if(e820.map[i].type!=E820_RAM && address<last && first<end)return 0;
 }
 return found;
}
static int destination(struct kimage *image, unsigned long address)
{
 int i;
 for(i=0;i<image->nr_segments;i++) {
  unsigned long start=(unsigned long)image->segments[i].mem;
  if(address>=start && address-start<image->segments[i].memsz)return 1;
 }
 return 0;
}
static unsigned long staging_page(struct kimage *image)
{
 unsigned long page;
 while((page=__get_free_page(GFP_KERNEL))!=0) {
  if(!destination(image,virt_to_phys((void*)page)))return page;
  /* Hold rejected pages until staging is finished; freeing immediately
   * would allow the allocator to hand back the same page indefinitely. */
  *(unsigned long*)page=image->rejected;
  image->rejected=page;
 }
 return 0;
}
static void release_rejected(struct kimage *image)
{
 while(image->rejected){unsigned long page=image->rejected;image->rejected=*(unsigned long*)page;free_page(page);}
}
void kimage_init(struct kimage *image)
{memset(image,0,sizeof(*image));image->entry=&image->head;image->last_entry=&image->head;}
static int add_entry(struct kimage *image,unsigned long entry)
{
 if(image->entry==image->last_entry){
  unsigned long page=staging_page(image);
  if(!page)return -ENOMEM;
  *(unsigned long*)page=0;
  *image->entry=virt_to_phys((void*)page)|IND_INDIRECTION;
  image->entry=(unsigned long*)page;
  image->last_entry=image->entry+PAGE_SIZE/sizeof(unsigned long)-1;
 }
 *image->entry++=entry;
 if(image->entry<=image->last_entry)*image->entry=0;
 return 0;
}
void kimage_free(struct kimage *image)
{
 unsigned long *ptr=&image->head,entry,previous=0;
 while((entry=*ptr)!=0 && !(entry&IND_DONE)){
  if(entry&IND_INDIRECTION){
   unsigned long next=entry&PAGE_MASK;
   if(previous)free_page(previous);
   previous=(unsigned long)phys_to_virt(next);ptr=(unsigned long*)previous;
  }else{if(entry&IND_SOURCE)free_page((unsigned long)phys_to_virt(entry&PAGE_MASK));ptr++;}
 }
 if(previous)free_page(previous);
 if(image->reboot_code_buffer)free_page((unsigned long)image->reboot_code_buffer);
 release_rejected(image);
}
int do_kexec(unsigned long start,long nr_segments,struct kexec_segment *arg_segments,struct kimage *image)
{
 int i,j,result=-EINVAL;unsigned long limit=max_low_pfn<<PAGE_SHIFT;
 struct kexec_segment *segments;
 if(nr_segments<1||nr_segments>16)return -EINVAL;
 segments=kmalloc(nr_segments*sizeof(*segments),GFP_KERNEL);
 if(!segments)return -ENOMEM;
 if(copy_from_user(segments,arg_segments,nr_segments*sizeof(*segments))){result=-EFAULT;goto out;}
 image->segments=segments;image->nr_segments=nr_segments;
 for(i=0;i<nr_segments;i++){
  unsigned long address=(unsigned long)segments[i].mem,size=segments[i].memsz;
  if(address<PAGE_SIZE||address>=limit||(address&~PAGE_MASK)||!size||(size&~PAGE_MASK)||size>limit-address||segments[i].bufsz>size)goto out;
  if(!usable(address,size))goto out;
  if(!access_ok(VERIFY_READ,segments[i].buf,segments[i].bufsz)){result=-EFAULT;goto out;}
  for(j=0;j<i;j++){
   unsigned long previous=(unsigned long)segments[j].mem;
   if(address<previous+segments[j].memsz && previous<address+size)goto out;
  }
 }
 if(!destination(image,start))goto out;
 for(i=0;i<nr_segments;i++){
  unsigned long offset,address=(unsigned long)segments[i].mem;
  result=add_entry(image,address|IND_DESTINATION);if(result)goto out;
  for(offset=0;offset<segments[i].memsz;offset+=PAGE_SIZE){
   unsigned long page=staging_page(image),size=0;
   if(!page){result=-ENOMEM;goto out;}
   memset((void*)page,0,PAGE_SIZE);
   if(offset<segments[i].bufsz){size=segments[i].bufsz-offset;if(size>PAGE_SIZE)size=PAGE_SIZE;}
   if(size&&copy_from_user((void*)page,(char*)segments[i].buf+offset,size)){free_page(page);result=-EFAULT;goto out;}
   result=add_entry(image,virt_to_phys((void*)page)|IND_SOURCE);
   if(result){free_page(page);goto out;}
  }
 }
 image->reboot_code_buffer=(void*)staging_page(image);
 if(!image->reboot_code_buffer){result=-ENOMEM;goto out;}
 result=add_entry(image,IND_DONE);if(result)goto out;
 image->start=start;release_rejected(image);
 out:
 image->segments=0;image->nr_segments=0;kfree(segments);return result;
}
