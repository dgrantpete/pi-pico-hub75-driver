#include "py/dynruntime.h"
#include <stdint.h>
#include <stddef.h>

#include "color.h"
#include "bitplanes.h"

#ifndef COLOR_BIT_DEPTH
#error "'COLOR_BIT_DEPTH' must be defined"
#endif

static mp_obj_t clear(mp_obj_t buffer_obj) {
    mp_buffer_info_t buffer;

    mp_get_buffer_raise(buffer_obj, &buffer, MP_BUFFER_WRITE);

    clear_buffer((uint8_t *)buffer.buf, buffer.len);

    return mp_const_none;
}

static mp_obj_t load_rgb888(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t input_buffer;
    mp_buffer_info_t output_buffer;
    mp_buffer_info_t gamma_lut_buffer;
    mp_buffer_info_t row_map_buffer;

    mp_get_buffer_raise(args[0], &input_buffer, MP_BUFFER_READ);
    mp_get_buffer_raise(args[1], &output_buffer, MP_BUFFER_WRITE);
    mp_get_buffer_raise(args[2], &gamma_lut_buffer, MP_BUFFER_READ);
    mp_get_buffer_raise(args[3], &row_map_buffer, MP_BUFFER_READ);

    const uint8_t *input_data = (const uint8_t *)input_buffer.buf;

    uint8_t *output_data = (uint8_t *)output_buffer.buf;
    size_t output_size = output_buffer.len;

    const uint8_t *gamma_lut = (const uint8_t *)gamma_lut_buffer.buf;

    const uint16_t *row_map = (const uint16_t *)row_map_buffer.buf;
    size_t chunk_count = row_map_buffer.len / sizeof(uint16_t);

    size_t pixel_count = (output_size / COLOR_BIT_DEPTH) * 2;

    size_t expected_input_size = pixel_count * 3;

    if (input_buffer.len != expected_input_size) {
        mp_raise_ValueError(MP_ERROR_TEXT("Input buffer does not match expected size for RGB888 data"));
    }

    if (gamma_lut_buffer.len != (1 << COLOR_BIT_DEPTH)) {
        mp_raise_ValueError(MP_ERROR_TEXT("Gamma LUT size does not match expected size for color bit depth"));
    }

    if (chunk_count < 2 || (chunk_count & 1) != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("Row map length must be even and at least 2"));
    }

    if (pixel_count % chunk_count != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("Row map length must divide pixel count evenly"));
    }

    load_rgb888_kernel(input_data, pixel_count, output_data, gamma_lut, row_map, chunk_count);

    return mp_const_none;
}

static mp_obj_t load_rgb565(size_t n_args, const mp_obj_t *args) {
    mp_buffer_info_t input_buffer;
    mp_buffer_info_t output_buffer;
    mp_buffer_info_t gamma_lut_buffer;
    mp_buffer_info_t row_map_buffer;

    mp_get_buffer_raise(args[0], &input_buffer, MP_BUFFER_READ);
    mp_get_buffer_raise(args[1], &output_buffer, MP_BUFFER_WRITE);
    mp_get_buffer_raise(args[2], &gamma_lut_buffer, MP_BUFFER_READ);
    mp_get_buffer_raise(args[3], &row_map_buffer, MP_BUFFER_READ);

    const uint8_t *input_data = (const uint8_t *)input_buffer.buf;

    uint8_t *output_data = (uint8_t *)output_buffer.buf;
    size_t output_size = output_buffer.len;

    const uint8_t *gamma_lut = (const uint8_t *)gamma_lut_buffer.buf;

    const uint16_t *row_map = (const uint16_t *)row_map_buffer.buf;
    size_t chunk_count = row_map_buffer.len / sizeof(uint16_t);

    size_t pixel_count = (output_size / COLOR_BIT_DEPTH) * 2;

    size_t expected_input_size = pixel_count * 2;

    if (input_buffer.len != expected_input_size) {
        mp_raise_ValueError(MP_ERROR_TEXT("Input buffer does not match expected size for RGB565 data"));
    }

    if (gamma_lut_buffer.len != (1 << COLOR_BIT_DEPTH)) {
        mp_raise_ValueError(MP_ERROR_TEXT("Gamma LUT size does not match expected size for color bit depth"));
    }

    if (chunk_count < 2 || (chunk_count & 1) != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("Row map length must be even and at least 2"));
    }

    if (pixel_count % chunk_count != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("Row map length must divide pixel count evenly"));
    }

    load_rgb565_kernel(input_data, pixel_count, output_data, gamma_lut, row_map, chunk_count);

    return mp_const_none;
}

static mp_obj_t pack_hsv_to_rgb565(mp_obj_t h_obj, mp_obj_t s_obj, mp_obj_t v_obj) {
    uint8_t h = (uint8_t)mp_obj_get_int(h_obj);
    uint8_t s = (uint8_t)mp_obj_get_int(s_obj);
    uint8_t v = (uint8_t)mp_obj_get_int(v_obj);

    uint16_t result = hsv_to_rgb565_kernel(h, s, v);

    return MP_OBJ_NEW_SMALL_INT(result);
}

static mp_obj_t pack_hsv_to_rgb888(mp_obj_t h_obj, mp_obj_t s_obj, mp_obj_t v_obj) {
    uint8_t h = (uint8_t)mp_obj_get_int(h_obj);
    uint8_t s = (uint8_t)mp_obj_get_int(s_obj);
    uint8_t v = (uint8_t)mp_obj_get_int(v_obj);

    uint8_t r, g, b;
    hsv_to_rgb_kernel(h, s, v, &r, &g, &b);

    // Pack as 0x00RRGGBB
    uint32_t packed = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;

    return mp_obj_new_int(packed);
}

static mp_obj_t hsv_to_rgb(mp_obj_t h_obj, mp_obj_t s_obj, mp_obj_t v_obj) {
    uint8_t h = (uint8_t)mp_obj_get_int(h_obj);
    uint8_t s = (uint8_t)mp_obj_get_int(s_obj);
    uint8_t v = (uint8_t)mp_obj_get_int(v_obj);

    uint8_t r, g, b;
    hsv_to_rgb_kernel(h, s, v, &r, &g, &b);

    mp_obj_t items[3] = {
        MP_OBJ_NEW_SMALL_INT(r),
        MP_OBJ_NEW_SMALL_INT(g),
        MP_OBJ_NEW_SMALL_INT(b)
    };

    return mp_obj_new_tuple(3, items);
}

static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(load_rgb888_obj, 4, 4, load_rgb888);
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(load_rgb565_obj, 4, 4, load_rgb565);
static MP_DEFINE_CONST_FUN_OBJ_1(clear_obj, clear);
static MP_DEFINE_CONST_FUN_OBJ_3(pack_hsv_to_rgb565_obj, pack_hsv_to_rgb565);
static MP_DEFINE_CONST_FUN_OBJ_3(pack_hsv_to_rgb888_obj, pack_hsv_to_rgb888);
static MP_DEFINE_CONST_FUN_OBJ_3(hsv_to_rgb_obj, hsv_to_rgb);

// IntelliSense stubs for module-specific QSTRs (generated at build time)
#ifdef __INTELLISENSE__
#define MP_QSTR_load_rgb888 (0)
#define MP_QSTR_load_rgb565 (0)
#define MP_QSTR_clear (0)
#define MP_QSTR_pack_hsv_to_rgb565 (0)
#define MP_QSTR_pack_hsv_to_rgb888 (0)
#define MP_QSTR_hsv_to_rgb (0)
#endif

mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY;

    mp_store_global(MP_QSTR_load_rgb888, MP_OBJ_FROM_PTR(&load_rgb888_obj));
    mp_store_global(MP_QSTR_load_rgb565, MP_OBJ_FROM_PTR(&load_rgb565_obj));
    mp_store_global(MP_QSTR_clear, MP_OBJ_FROM_PTR(&clear_obj));
    mp_store_global(MP_QSTR_pack_hsv_to_rgb565, MP_OBJ_FROM_PTR(&pack_hsv_to_rgb565_obj));
    mp_store_global(MP_QSTR_pack_hsv_to_rgb888, MP_OBJ_FROM_PTR(&pack_hsv_to_rgb888_obj));
    mp_store_global(MP_QSTR_hsv_to_rgb, MP_OBJ_FROM_PTR(&hsv_to_rgb_obj));

    MP_DYNRUNTIME_INIT_EXIT;
}
