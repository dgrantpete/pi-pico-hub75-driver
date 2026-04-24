#include "bitplanes.h"

#ifndef COLOR_BIT_DEPTH
#error "'COLOR_BIT_DEPTH' must be defined"
#endif

// The least significant bits correspond with the lowest pin numbers
// e.g. R1 is connected to pin 0, G1 to pin 1, B1 to pin 2, R2 to pin 3, etc.
enum {
    R1_BIT = 0b000001,
    G1_BIT = 0b000010,
    B1_BIT = 0b000100,
    R2_BIT = 0b001000,
    G2_BIT = 0b010000,
    B2_BIT = 0b100000
};

void pack_pixel_pair(
    uint8_t r1, uint8_t g1, uint8_t b1,
    uint8_t r2, uint8_t g2, uint8_t b2,
    uint8_t *initial_bitplane,
    size_t bitplane_size
) {
    for (size_t bitplane_index = 0; bitplane_index < COLOR_BIT_DEPTH; bitplane_index++) {
        uint8_t packed_pixel = (
            ((r1 >> bitplane_index) & 1u) * R1_BIT |
            ((g1 >> bitplane_index) & 1u) * G1_BIT |
            ((b1 >> bitplane_index) & 1u) * B1_BIT |
            ((r2 >> bitplane_index) & 1u) * R2_BIT |
            ((g2 >> bitplane_index) & 1u) * G2_BIT |
            ((b2 >> bitplane_index) & 1u) * B2_BIT
        );

        initial_bitplane[bitplane_index * bitplane_size] = packed_pixel;
    }
}

void load_rgb888_kernel(
    const uint8_t *input_data,
    size_t pixel_count,
    uint8_t *output_data,
    const uint8_t *gamma_lut,
    const uint16_t *row_map,
    size_t chunk_count
) {
    const size_t bitplane_size = pixel_count / 2;
    const size_t chunk_size = pixel_count / chunk_count;
    const size_t half_chunk_count = chunk_count / 2;

    for (size_t top_chunk = 0; top_chunk < half_chunk_count; top_chunk++) {
        const uint8_t *top_row_data = input_data + (size_t)row_map[top_chunk] * chunk_size * 3;
        const uint8_t *bottom_row_data = input_data + (size_t)row_map[top_chunk + half_chunk_count] * chunk_size * 3;
        uint8_t *chunk_output = output_data + top_chunk * chunk_size;

        for (size_t within_chunk = 0; within_chunk < chunk_size; within_chunk++) {
            const size_t top_offset = within_chunk * 3;
            const size_t bottom_offset = within_chunk * 3;

            const uint32_t r1 = gamma_lut[top_row_data[top_offset]];
            const uint32_t g1 = gamma_lut[top_row_data[top_offset + 1]];
            const uint32_t b1 = gamma_lut[top_row_data[top_offset + 2]];

            const uint32_t r2 = gamma_lut[bottom_row_data[bottom_offset]];
            const uint32_t g2 = gamma_lut[bottom_row_data[bottom_offset + 1]];
            const uint32_t b2 = gamma_lut[bottom_row_data[bottom_offset + 2]];

            pack_pixel_pair(r1, g1, b1, r2, g2, b2, chunk_output + within_chunk, bitplane_size);
        }
    }
}

void load_rgb565_kernel(
    const uint8_t *input_data,
    size_t pixel_count,
    uint8_t *output_data,
    const uint8_t *gamma_lut,
    const uint16_t *row_map,
    size_t chunk_count
) {
    const size_t bitplane_size = pixel_count / 2;
    const size_t chunk_size = pixel_count / chunk_count;
    const size_t half_chunk_count = chunk_count / 2;

    for (size_t top_chunk = 0; top_chunk < half_chunk_count; top_chunk++) {
        const uint8_t *top_row_data = input_data + (size_t)row_map[top_chunk] * chunk_size * 2;
        const uint8_t *bottom_row_data = input_data + (size_t)row_map[top_chunk + half_chunk_count] * chunk_size * 2;
        uint8_t *chunk_output = output_data + top_chunk * chunk_size;

        for (size_t within_chunk = 0; within_chunk < chunk_size; within_chunk++) {
            const size_t top_offset = within_chunk * 2;
            const size_t bottom_offset = within_chunk * 2;

            uint32_t r1 = top_row_data[top_offset + 1] & 0b11111000;
            uint32_t g1 = (top_row_data[top_offset + 1] << 5 | (top_row_data[top_offset] >> 3)) & 0b11111100;
            uint32_t b1 = (top_row_data[top_offset] << 3) & 0b11111000;

            uint32_t r2 = bottom_row_data[bottom_offset + 1] & 0b11111000;
            uint32_t g2 = (bottom_row_data[bottom_offset + 1] << 5 | (bottom_row_data[bottom_offset] >> 3)) & 0b11111100;
            uint32_t b2 = (bottom_row_data[bottom_offset] << 3) & 0b11111000;

            // Replicating the MSBs to fill the empty LSBs gives us a scaling factor
            // from minimum to maximum brightness at the cost of slight nonlinearity
            r1 |= (r1 >> 5);
            g1 |= (g1 >> 6);
            b1 |= (b1 >> 5);

            r2 |= (r2 >> 5);
            g2 |= (g2 >> 6);
            b2 |= (b2 >> 5);

            // Apply gamma correction after full 8-bit reconstruction
            r1 = gamma_lut[r1];
            g1 = gamma_lut[g1];
            b1 = gamma_lut[b1];

            r2 = gamma_lut[r2];
            g2 = gamma_lut[g2];
            b2 = gamma_lut[b2];

            pack_pixel_pair(r1, g1, b1, r2, g2, b2, chunk_output + within_chunk, bitplane_size);
        }
    }
}

void clear_buffer(uint8_t *data, size_t size) {
    // Use 'volatile' to prevent compiler from optimizing this into memset (it isn't available in natmod context)
    volatile uint8_t *volatile_data = data;
    for (size_t index = 0; index < size; index++) {
        volatile_data[index] = 0;
    }
}
