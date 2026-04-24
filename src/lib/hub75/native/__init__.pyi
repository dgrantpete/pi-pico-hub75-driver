"""Native C helpers for HUB75 pixel conversion.

These functions do the heavy lifting that would be too slow in pure Python:
converting RGB888/RGB565 pixel data into the bitplane format consumed by the
PIO, clearing bitplane buffers, and packing HSV triples into RGB. At runtime
the `hub75.native` package dispatches to the appropriate architecture-specific
compiled module (armv6m for RP2040, armv7emsp for RP2350); this `.pyi` file
documents the shared interface that both compiled variants implement.

In most cases you should call these indirectly through `Hub75Driver` (which
wires up the gamma LUT and row map for you). The HSV helpers are convenient
for generating effects that feed into `load_rgb888` / `load_rgb565`.
"""


def load_rgb888(
    input_data: memoryview | bytes | bytearray,
    output_data: bytearray | memoryview,
    gamma_lut: bytearray | bytes,
    row_map: memoryview | bytes | bytearray
) -> None:
    """Convert RGB888 pixel data into bitplanes and write it to `output_data`.

    Pairs of rows (top half + bottom half of the panel) are packed into 6-bit
    words `(R1, G1, B1, R2, G2, B2)` — one bit per channel per bitplane, eight
    bitplanes total (one per bit of color depth). Gamma is applied per channel
    during conversion.

    Args:
        input_data: Source RGB888 buffer of `pixel_count * 3` bytes, three
            bytes per pixel in R, G, B order.
        output_data: Destination bitplane buffer of
            `row_address_count * shift_register_depth * 8` bytes
            (i.e. `(pixel_count / 2) * COLOR_BIT_DEPTH`). Must be writable.
        gamma_lut: 256-byte gamma lookup table, typically produced by
            `Hub75Driver._create_gamma_lut`. Indexed by 8-bit channel value.
        row_map: `uint16` (`array('H', ...)`) remap from logical pixel chunks
            to physical shift-register chunks. Length must be even and at
            least 2, and must divide the pixel count evenly. Each entry must
            be in `[0, len(row_map))`.

    Raises:
        ValueError: If the input/output/gamma/row_map sizes don't satisfy the
            constraints above.
    """
    ...


def load_rgb565(
    input_data: memoryview | bytes | bytearray,
    output_data: bytearray | memoryview,
    gamma_lut: bytearray | bytes,
    row_map: memoryview | bytes | bytearray
) -> None:
    """Convert RGB565 pixel data into bitplanes and write it to `output_data`.

    Same contract as `load_rgb888`, but the input is RGB565 (2 bytes per pixel,
    little-endian: low byte = `GGGBBBBB`, high byte = `RRRRRGGG` — matching
    MicroPython's `framebuf.RGB565`). Each 5- or 6-bit channel is expanded to 8
    bits (MSBs replicated into the empty LSBs) before gamma correction.

    Args:
        input_data: Source RGB565 buffer of `pixel_count * 2` bytes.
        output_data: Destination bitplane buffer; see `load_rgb888`.
        gamma_lut: 256-byte gamma lookup table; see `load_rgb888`.
        row_map: Pixel-chunk remap array; see `load_rgb888`.

    Raises:
        ValueError: If the buffer sizes or `row_map` constraints are violated.
    """
    ...


def clear(buffer: bytearray | memoryview) -> None:
    """Zero every byte of `buffer` in place.

    Typically used to blank an inactive bitplane buffer without allocating a
    fresh one. Works with any writable byte buffer.
    """
    ...


def pack_hsv_to_rgb565(hue: int, saturation: int, value: int) -> int:
    """Convert an HSV triple into a packed 16-bit RGB565 color.

    Args:
        hue: Hue in `[0, 255]` (the 8-bit range wraps the full color wheel).
        saturation: Saturation in `[0, 255]`, where 0 is grayscale and 255 is
            fully saturated.
        value: Value/brightness in `[0, 255]`, where 0 is black and 255 is
            full brightness.

    Returns:
        The color packed as a `uint16` in RGB565 layout (R:5, G:6, B:5).
    """
    ...


def pack_hsv_to_rgb888(hue: int, saturation: int, value: int) -> int:
    """Convert an HSV triple into a packed 24-bit RGB888 color.

    Args:
        hue: Hue in `[0, 255]` (wraps the full color wheel).
        saturation: Saturation in `[0, 255]`.
        value: Value/brightness in `[0, 255]`.

    Returns:
        The color packed as `0x00RRGGBB` — i.e. R in the upper 8 bits of the
        low 24 bits, then G, then B. Suitable for writing directly into an
        RGB888 byte buffer after splitting into individual bytes.
    """
    ...


def hsv_to_rgb(hue: int, saturation: int, value: int) -> tuple[int, int, int]:
    """Convert an HSV triple into separate 8-bit R, G, B components.

    Args:
        hue: Hue in `[0, 255]` (wraps the full color wheel).
        saturation: Saturation in `[0, 255]`.
        value: Value/brightness in `[0, 255]`.

    Returns:
        A `(r, g, b)` tuple of integers, each in `[0, 255]`.
    """
    ...