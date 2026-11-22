/* Bounded draw records. The caller supplies all memory and a baseline reader. */
#include "../draw-stream/draw_queue.h"

static void put16(uint8_t *p, uint16_t n) { p[0] = (uint8_t)n; p[1] = (uint8_t)(n >> 8); }
static void put32(uint8_t *p, uint32_t n) { put16(p, (uint16_t)n); put16(p + 2, (uint16_t)(n >> 16)); }

void ds100_draw_init(ds100_draw_queue *s, uint8_t *queue, uint32_t capacity) {
    unsigned i;
    uint8_t *p = (uint8_t *)s;
    for (i = 0; i < sizeof(*s); ++i) p[i] = 0;
    s->queue = queue; s->capacity = capacity;
}

void ds100_draw_start(ds100_draw_queue *s, uint32_t epoch) {
    uint8_t *queue = s->queue;
    uint32_t capacity = s->capacity;
    ds100_draw_init(s, queue, capacity);
    s->epoch = epoch; s->mode = DRAW_BASELINE; s->reset_pending = 1;
}

static int reserve(ds100_draw_queue *s, unsigned bytes) {
    if (!s || s->mode != DRAW_ACTIVE) return 0;
    if (!s->queue || bytes > s->capacity - s->used) {
        s->mode = DRAW_FAULT; s->fault = DRAW_OVERFLOW;
        return 0;
    }
    ++s->records; s->dirty = 1;
    if (s->used + bytes > s->peak_used) s->peak_used = s->used + bytes;
    return 1;
}

static void q8(ds100_draw_queue *s, uint8_t b) {
    s->queue[s->write_at++] = b;
    if (s->write_at == s->capacity) s->write_at = 0;
    ++s->used;
}
static void q16(ds100_draw_queue *s, uint16_t n) { q8(s, (uint8_t)n); q8(s, (uint8_t)(n >> 8)); }
static void header(ds100_draw_queue *s, uint8_t op, uint8_t flags, uint16_t length) {
    q8(s, op); q8(s, flags); q16(s, length);
}

static int bounds(ds100_draw_queue *s, uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
    if (!s || s->mode != DRAW_ACTIVE) return 0;
    /* Stock callers use inclusive endpoints at width/height. Preserve source
     * strides and clip the non-visible edge, rather than discard the whole row.
     * Actual controller edge behavior must also pass physical comparisons.
     */
    if (x0 > x1 || y0 > y1 || x1 > 480u || y1 > 320u) {
        s->mode = DRAW_FAULT; s->fault = DRAW_GEOMETRY;
        return 0;
    }
    if (x0 >= 480u || y0 >= 320u) { ++s->clipped; return 0; }
    if (x1 == 480u || y1 == 320u) ++s->clipped;
    return 1;
}

void ds100_draw_point(ds100_draw_queue *s, uint16_t x, uint16_t y, uint16_t color) {
    if (!bounds(s, x, y, x, y) || !reserve(s, 10)) return;
    header(s, 1, 0, 6); q16(s, x); q16(s, y); q16(s, color);
}

void ds100_draw_fill(ds100_draw_queue *s, uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t color) {
    if (!bounds(s, x0, y0, x1, y1) || !reserve(s, 14)) return;
    if (x1 == 480u) x1 = 479u;
    if (y1 == 320u) y1 = 319u;
    header(s, 2, 0, 10);
    q16(s, x0); q16(s, y0); q16(s, x1); q16(s, y1); q16(s, color);
}

static unsigned span_size(const uint16_t *pixels, unsigned width, unsigned *encoding,
                           uint16_t *palette, unsigned *colors) {
    unsigned i, start = 0, runs = 0, tokens = 0, color_count = 0, valid_palette = 1, best;
    for (i = 1; i <= width; ++i) if (i == width || pixels[i] != pixels[start]) {
        unsigned index, length = i - start;
        ++runs;
        tokens += 1u + (length >= 16u) + (length >= 144u);
        if (valid_palette) {
            for (index = 0; index < color_count && palette[index] != pixels[start]; ++index) {}
            if (index == color_count) {
                if (color_count == 16u) valid_palette = 0;
                else palette[color_count++] = pixels[start];
            }
        }
        start = i;
    }
    *encoding = runs * 4u < width * 2u;
    best = *encoding ? runs * 4u : width * 2u;
    if (valid_palette && 1u + color_count * 2u + tokens < best) {
        *encoding = 2; best = 1u + color_count * 2u + tokens;
    }
    *colors = color_count;
    return best;
}

static unsigned palette_index(const uint16_t *palette, unsigned colors, uint16_t value) {
    unsigned index;
    for (index = 0; index < colors && palette[index] != value; ++index) {}
    return index;
}

void ds100_draw_span(ds100_draw_queue *s, uint16_t x0, uint16_t x1, uint16_t y, const uint16_t *pixels) {
    if (!pixels || !bounds(s, x0, y, x1, y)) return;
    if (x1 == 480u) x1 = 479u;
    ds100_draw_pixels(s, x0, y, (uint16_t)(x1 - x0 + 1u), 0, 0, pixels);
}

__attribute__((noinline)) void ds100_minimal_stable_pixels(ds100_draw_queue *s, uint16_t x, uint16_t y, uint16_t width,
                        int vertical, int reverse, const uint16_t *pixels) {
    unsigned encoding, bytes, i, start;
    unsigned colors;
    uint16_t stable[480], palette[16];
    uint16_t previous, value;
    if (!width || !pixels || !bounds(s, x, y, (uint16_t)(x + (vertical ? 0u : width - 1u)),
                                     (uint16_t)(y + (vertical ? width - 1u : 0u)))) return;
    if ((vertical && (unsigned)y + width > 320u) || (!vertical && (unsigned)x + width > 480u)) {
        s->mode = DRAW_FAULT; s->fault = DRAW_GEOMETRY; return;
    }
    /* The length pass and the encoding pass must see the same pixels even if
     * a caller supplied memory that an interrupt can update.
     */
    for (i = 0; i < width; ++i) stable[i] = pixels[i];
    pixels = stable;
    bytes = span_size(pixels, width, &encoding, palette, &colors);
    if (!reserve(s, 12u + bytes)) return;
    header(s, vertical ? 5u : 3u, 0, (uint16_t)(8u + bytes));
    q16(s, x); q16(s, y); q16(s, width);
    q8(s, (uint8_t)encoding); q8(s, 0);
    if (encoding == 2u) {
        q8(s, (uint8_t)colors);
        for (i = 0; i < colors; ++i) q16(s, palette[i]);
    }
    if (!encoding) {
        for (i = 0; i < width; ++i) q16(s, pixels[reverse ? width - 1u - i : i]);
    } else {
        start = 0;
        previous = pixels[reverse ? width - 1u : 0u];
        for (i = 1; i <= width; ++i) {
            value = i == width ? previous : pixels[reverse ? width - 1u - i : i];
            if (i == width || value != previous) {
                unsigned length = i - start;
                if (encoding == 1u) { q16(s, (uint16_t)length); q16(s, previous); }
                else {
                    q8(s, (uint8_t)((palette_index(palette, colors, previous) << 4) |
                                   (length < 16u ? length - 1u : 15u)));
                    if (length >= 16u) {
                        length -= 16u;
                        if (length >= 128u) { q8(s, (uint8_t)((length & 127u) | 128u)); length >>= 7; }
                        q8(s, (uint8_t)length);
                    }
                }
                start = i; previous = value;
            }
        }
    }
}

/* Fixed RAW records use a two-pixel snapshot; the wide encoder is unchanged. */
void ds100_minimal_tiny_pixels(ds100_draw_queue *s,uint16_t x,uint16_t y,uint16_t width,
                               int vertical,int reverse,const uint16_t *pixels) {
    uint16_t small[2];unsigned i;
    if(width>2u) { ds100_minimal_stable_pixels(s,x,y,width,vertical,reverse,pixels);return; }
    if(!width || !pixels || !bounds(s,x,y,(uint16_t)(x+(vertical?0u:width-1u)),
                                    (uint16_t)(y+(vertical?width-1u:0u))))return;
    if((vertical && (unsigned)y+width>320u) || (!vertical && (unsigned)x+width>480u)) {
        s->mode=DRAW_FAULT;s->fault=DRAW_GEOMETRY;return;
    }
    for(i=0;i<width;++i)small[i]=pixels[i];
    if(!reserve(s,12u+width*2u))return;
    header(s,vertical?5u:3u,0,(uint16_t)(8u+width*2u));
    q16(s,x);q16(s,y);q16(s,width);q8(s,0);q8(s,0);
    for(i=0;i<width;++i)q16(s,small[reverse?width-1u-i:i]);
}
#if !defined(__arm__) && !defined(__thumb__)
void ds100_draw_pixels(ds100_draw_queue *s,uint16_t x,uint16_t y,uint16_t width,
                        int vertical,int reverse,const uint16_t *pixels) {
    if(width>2u)ds100_minimal_stable_pixels(s,x,y,width,vertical,reverse,pixels);
    else ds100_minimal_tiny_pixels(s,x,y,width,vertical,reverse,pixels);
}
#endif

void ds100_draw_clear(ds100_draw_queue *s, uint16_t color) {
    if (!reserve(s, 6)) return;
    header(s, 0, 0, 2); q16(s, color);
}

void ds100_draw_bitmap(ds100_draw_queue *s, uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1,
                        const uint16_t *pixels) {
    unsigned y, stride = (unsigned)x1 - x0 + 1u;
    if (!pixels || !bounds(s, x0, y0, x1, y1)) return;
    for (y = y0; y <= y1 && s->mode == DRAW_ACTIVE; ++y)
        ds100_draw_span(s, x0, x1, (uint16_t)y, pixels + (y - y0) * stride);
}

void ds100_draw_present(ds100_draw_queue *s) {
    if (!s || !s->dirty || s->mode != DRAW_ACTIVE) return;
    if (!reserve(s, 8)) return;
    ++s->frame_id;
    header(s, 4, 0, 4); q16(s, (uint16_t)s->frame_id); q16(s, (uint16_t)(s->frame_id >> 16));
    s->dirty = 0;
}

static uint8_t peek(const ds100_draw_queue *s, unsigned offset) {
    unsigned at = s->read_at + offset;
    if (at >= s->capacity) at -= s->capacity;
    return s->queue[at];
}

static unsigned baseline_span(uint8_t *out, size_t capacity, unsigned y, const uint16_t *pixels) {
    unsigned encoding, bytes, i, start, offset = 12, colors;
    uint16_t palette[16];
    bytes = span_size(pixels, 480, &encoding, palette, &colors);
    if (capacity < 12u + bytes) return 0;
    out[0] = 3; out[1] = 0; put16(out + 2, (uint16_t)(8u + bytes));
    put16(out + 4, 0); put16(out + 6, (uint16_t)y); put16(out + 8, 480);
    out[10] = (uint8_t)encoding; out[11] = 0;
    if (encoding == 2u) {
        out[offset++] = (uint8_t)colors;
        for (i = 0; i < colors; ++i) { put16(out + offset, palette[i]); offset += 2; }
    }
    if (!encoding) {
        for (i = 0; i < 480; ++i) put16(out + offset + i * 2u, pixels[i]);
    } else {
        start = 0;
        for (i = 1; i <= 480; ++i) if (i == 480 || pixels[i] != pixels[start]) {
            unsigned length = i - start;
            if (encoding == 1u) {
                put16(out + offset, (uint16_t)length); put16(out + offset + 2, pixels[start]); offset += 4;
            } else {
                out[offset++] = (uint8_t)((palette_index(palette, colors, pixels[start]) << 4) |
                                        (length < 16u ? length - 1u : 15u));
                if (length >= 16u) {
                    length -= 16u;
                    if (length >= 128u) { out[offset++] = (uint8_t)((length & 127u) | 128u); length >>= 7; }
                    out[offset++] = (uint8_t)length;
                }
            }
            start = i;
        }
    }
    return 12u + bytes;
}

static __attribute__((noinline)) unsigned baseline_records(ds100_draw_queue *s, uint8_t *out, size_t capacity,
        ds100_read_row reader, void *context, unsigned *record_count) {
    unsigned count = 0, size = 20, n;
    uint16_t row[480];
    if (s->reset_pending) {
        out[size] = 0; out[size + 1] = 0; put16(out + size + 2, 2); put16(out + size + 4, 0);
        size += 6; ++count;
    }
    while (s->baseline_y < 320u) {
        /* Do not let a highly compressed full-screen packet exceed the
         * receiver's work budget. The first packet also contains CLEAR.
         */
        if (count >= 120u) break;
        reader(context, (uint16_t)s->baseline_y, row);
        n = baseline_span(out + size, capacity - size - 8u, s->baseline_y, row);
        if (!n) break;
        size += n; ++count; ++s->baseline_y;
    }
    if (s->baseline_y == 320u) {
        out[size] = 4; out[size + 1] = 1; put16(out + size + 2, 4); put32(out + size + 4, 0);
        size += 8; ++count; s->mode = DRAW_ACTIVE;
    }
    *record_count = count;
    return size;
}

static void copy_linear(volatile uint8_t *out,const volatile uint8_t *in,unsigned n) {
    while(n>=4u) { out[0]=in[0];out[1]=in[1];out[2]=in[2];out[3]=in[3];out+=4;in+=4;n-=4; }
    while(n--)*out++=*in++;
}

size_t ds100_draw_packet(ds100_draw_queue *s, uint8_t *out, size_t capacity,
                          ds100_read_row reader, void *context) {
    unsigned count = 0, size = 20, n, presentations = 0, work = 0;
    uint32_t first;
    if (!s || !out || capacity < 1000u || capacity > 4076u ||
            s->mode == DRAW_OFF || s->mode == DRAW_FAULT) return 0;
    first = s->next_record;
    out[0] = 'D'; out[1] = 'R'; out[2] = 'W'; out[3] = '1';
    put32(out + 4, s->epoch); put32(out + 8, first);
    put16(out + 16, (uint16_t)s->reset_pending); out[18] = out[19] = 0;
    if (s->mode == DRAW_BASELINE) {
        if (!reader) return 0;
        size = baseline_records(s, out, capacity, reader, context, &count);
    } else {
        while (s->used >= 4u && count < 4096u) {
            uint8_t op = peek(s, 0);
            n = 4u + (unsigned)(peek(s, 2) | ((unsigned)peek(s, 3) << 8));
            if (n > s->used || n > 1000u) { s->mode = DRAW_FAULT; s->fault = DRAW_GEOMETRY; return 0; }
            if (size + n > capacity || (op == 4u && presentations == 4u)) break;
            /* A packet can contain many CLEAR/FILL records. Keep its decoded
             * work within the same bound as the Python receiver.
             */
            {
                unsigned pixels = 0;
                if (op == 0u) pixels = 153600u;
                else if (op == 1u) pixels = 1;
                else if (op == 3u || op == 5u) pixels = (unsigned)(peek(s, 8) | ((unsigned)peek(s, 9) << 8));
                else if (op == 2u) {
                    unsigned x0 = (unsigned)(peek(s, 4) | ((unsigned)peek(s, 5) << 8));
                    unsigned y0 = (unsigned)(peek(s, 6) | ((unsigned)peek(s, 7) << 8));
                    unsigned x1 = (unsigned)(peek(s, 8) | ((unsigned)peek(s, 9) << 8));
                    unsigned y1 = (unsigned)(peek(s, 10) | ((unsigned)peek(s, 11) << 8));
                    pixels = (x1 - x0 + 1u) * (y1 - y0 + 1u);
                }
                if (work + pixels > 614400u) break;
                work += pixels;
            }
            {
                unsigned first=s->capacity-s->read_at;
                if(first>n)first=n;
                copy_linear(out+size,s->queue+s->read_at,first);
                if(first<n)copy_linear(out+size+first,s->queue,n-first);
                s->read_at+=n;if(s->read_at>=s->capacity)s->read_at-=s->capacity;
                size+=n;
            }
            s->used -= n; --s->records; ++count;
            if (op == 4u) ++presentations;
        }
    }
    put32(out + 12, count);
    s->next_record += count; s->reset_pending = 0;
    return size;
}
