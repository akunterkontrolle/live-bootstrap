/* SPDX-FileCopyrightText: 2026 akunterkontrolle
 * SPDX-License-Identifier: MIT
 * Fixed-Huffman gzip encoder with a bounded 32KiB LZ window.
 * A single candidate per three-byte hash keeps image packing linear.
 */
struct gzip_output {
    unsigned char *data;
    unsigned int length, capacity, bits, count;
    int failed;
};
static void gzip_byte(struct gzip_output *out, unsigned int byte)
{
    if (out->length == out->capacity) { out->failed = 1; return; }
    out->data[out->length++] = byte;
}
static void gzip_bits(struct gzip_output *out, unsigned int value, unsigned int count)
{
    out->bits |= value << out->count;
    out->count += count;
    while (out->count >= 8) {
        gzip_byte(out, out->bits & 255);
        out->bits >>= 8;
        out->count -= 8;
    }
}
static void gzip_symbol(struct gzip_output *out, unsigned int symbol)
{
    unsigned int code, count, reverse = 0, i;
    if (symbol < 144) { code = symbol + 48; count = 8; }
    else if (symbol < 256) { code = symbol - 144 + 400; count = 9; }
    else if (symbol < 280) { code = symbol - 256; count = 7; }
    else { code = symbol - 280 + 192; count = 8; }
    for (i = 0; i < count; i++) { reverse = (reverse << 1) | (code & 1); code >>= 1; }
    gzip_bits(out, reverse, count);
}
static unsigned int gzip_crc_byte(unsigned int crc, unsigned int byte,
                                  unsigned int *table)
{ return table[(crc ^ byte) & 255] ^ (crc >> 8); }
static void gzip_match(struct gzip_output *out, unsigned int length,
                       unsigned int distance)
{
    static const unsigned short lengths[29] = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
    static const unsigned char lextra[29] = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
    static const unsigned short distances[30] = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
    unsigned int i, extra, reverse = 0, j;
    for (i = 28; lengths[i] > length; i--);
    gzip_symbol(out, 257 + i);
    gzip_bits(out, length - lengths[i], lextra[i]);
    for (i = 29; distances[i] > distance; i--);
    for (j = 0; j < 5; j++) reverse = (reverse << 1) | ((i >> j) & 1);
    gzip_bits(out, reverse, 5);
    extra = i < 4 ? 0 : i / 2 - 1;
    gzip_bits(out, distance - distances[i], extra);
}
static unsigned int gzip_hash(unsigned char *p)
{ return (p[0] * 251u + p[1] * 31u + p[2]) & 65535; }
static unsigned int gzip_image(unsigned char *input, unsigned int size,
                               unsigned char *output, unsigned int capacity)
{
    struct gzip_output out;
    unsigned int table[256], *hash;
    unsigned int i, j, crc = 0xffffffffu, offset = 0;
    unsigned char header[10] = {31,139,8,0,0,0,0,0,0,3};
    hash = malloc(65536 * sizeof(*hash));
    if (!hash) return 0;
    for (i = 0; i < 65536; i++) hash[i] = 0xffffffffu;
    memset(&out, 0, sizeof(out));
    out.data = output; out.capacity = capacity;
    for (i = 0; i < 256; i++) {
        unsigned int c = i;
        for (j = 0; j < 8; j++) c = (c >> 1) ^ ((c & 1) ? 0xedb88320u : 0);
        table[i] = c;
    }
    for (i = 0; i < 10; i++) gzip_byte(&out, header[i]);
    gzip_bits(&out, 3, 3);
    while (offset < size && !out.failed) {
        unsigned int length = 0, previous = 0xffffffffu, key = 0;
        if (size - offset >= 3) {
            key = gzip_hash(input + offset);
            previous = hash[key]; hash[key] = offset;
            if (previous != 0xffffffffu && offset - previous <= 32768) {
                unsigned int maximum = size - offset;
                if (maximum > 258) maximum = 258;
                while (length < maximum && input[previous + length] == input[offset + length]) length++;
            }
        }
        if (length >= 3) {
            gzip_match(&out, length, offset - previous);
            for (i = 0; i < length; i++) crc = gzip_crc_byte(crc, input[offset + i], table);
            if (size - offset - length >= 3) hash[gzip_hash(input + offset + length - 1)] = offset + length - 1;
            offset += length;
        } else {
            unsigned int byte = input[offset++];
            crc = gzip_crc_byte(crc, byte, table);
            gzip_symbol(&out, byte);
        }
    }
    gzip_symbol(&out, 256);
    if (out.count) gzip_byte(&out, out.bits & 255);
    crc ^= 0xffffffffu;
    for (i = 0; i < 4; i++) gzip_byte(&out, crc >> (8 * i));
    for (i = 0; i < 4; i++) gzip_byte(&out, size >> (8 * i));
    free(hash);
    return out.failed ? 0 : out.length;
}
