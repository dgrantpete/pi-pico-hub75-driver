#ifndef BITPLANES_H
#define BITPLANES_H

#include <stdint.h>
#include <stddef.h>

void pack_pixel_pair(
    uint8_t r1, uint8_t g1, uint8_t b1,
    uint8_t r2, uint8_t g2, uint8_t b2,
    uint8_t *initial_bitplane,
    size_t bitplane_size
);

void load_rgb888_kernel(
    const uint8_t *input_data,
    size_t pixel_count,
    uint8_t *output_data,
    const uint8_t *gamma_lut,
    const uint16_t *row_map,
    size_t chunk_count
);

void load_rgb565_kernel(
    const uint8_t *input_data,
    size_t pixel_count,
    uint8_t *output_data,
    const uint8_t *gamma_lut,
    const uint16_t *row_map,
    size_t chunk_count
);

void clear_buffer(uint8_t *data, size_t size);

#endif
