import _thread
import math
import micropython
from time import sleep_ms
from machine import Pin
from hub75 import Hub75Driver, row_addressing, gamma as gamma_module
from hub75.effects import render_plasma_frame, render_fire_frame, render_spiral_frame, render_balatro_frame

# CONFIGURATION

WIDTH = 64
HEIGHT = 64

# First GPIO pin index for pixel data
# Requires 6 contiguous pins for R1, G1, B1, R2, G2, B2
BASE_DATA_PIN = 0

# First GPIO pin index for clock and latch
# Requires 2 contiguous pins for CLK and LAT
BASE_CLOCK_PIN = 6

# GPIO pin index for output enable (OE)
OUTPUT_ENABLE_PIN = 8

# First GPIO pin index for address lines
# Requires contiguous pins for each address, e.g. A, B, C, D, ...
# For typical indoor panels, this is usually HEIGHT / 2 address lines
BASE_ADDRESS_PIN = 9

# Speed of the data sent to the panel
# Different panels have different maximum speeds, this value can be adjusted as needed
# Artifacting will occur if the speed is too high
DATA_FREQUENCY = 20_000_000

# Adjustable parameters to control effects
SPIN_SPEED = 7
WARP_AMOUNT = 12

# GLOBAL STATE

# Global driver
driver = None

# Effect buffers (allocated on first use)
_rgb_buffer = None
_fire_buffer = None
_spiral_tables = None

# Effect control state
_running = False
_effect_mode = None

def bit_length(n: int) -> int:
    length = 0
    while n > 0:
        n >>= 1
        length += 1
    return length

def _init():
    global driver
    if driver is not None:
        return

    print("Initializing driver...")
    driver = Hub75Driver(
        row_addressing=row_addressing.Binary(
            base_pin=Pin(BASE_ADDRESS_PIN),
            bit_count=bit_length(HEIGHT // 2 - 1)
        ),
        shift_register_depth=WIDTH,
        base_data_pin=Pin(BASE_DATA_PIN),
        base_clock_pin=Pin(BASE_CLOCK_PIN),
        output_enable_pin=Pin(OUTPUT_ENABLE_PIN),
        data_frequency=DATA_FREQUENCY
    )
    print("Ready!")

def _ensure_buffers():
    global _rgb_buffer, _fire_buffer
    if _rgb_buffer is None:
        _rgb_buffer = bytearray(WIDTH * HEIGHT * 3)
        _fire_buffer = bytearray(WIDTH * HEIGHT)

def _ensure_spiral_tables():
    global _spiral_tables
    if _spiral_tables is not None:
        return

    center_x = WIDTH // 2
    center_y = HEIGHT // 2
    angle_table = bytearray(WIDTH * HEIGHT)
    radius_table = bytearray(WIDTH * HEIGHT)

    max_radius = math.sqrt(center_x * center_x + center_y * center_y)
    two_pi = 2 * math.pi

    for y in range(HEIGHT):
        for x in range(WIDTH):
            dx = x - center_x
            dy = y - center_y
            pixel_index = y * WIDTH + x

            angle = math.atan2(dy, dx)
            angle_table[pixel_index] = int((angle + math.pi) * 255 / two_pi)

            radius = math.sqrt(dx * dx + dy * dy)
            radius_table[pixel_index] = int(radius * 255 / max_radius)

    _spiral_tables = (angle_table, radius_table)

def _init_fire():
    assert _fire_buffer is not None
    for i in range(len(_fire_buffer)):
        _fire_buffer[i] = 0
    for x in range(WIDTH):
        _fire_buffer[(HEIGHT - 1) * WIDTH + x] = 36

@micropython.native
def _render_effect(mode, rgb_buffer, fire_buffer, frame_time):
    """Render a single frame of the given effect."""
    current_spin_speed = SPIN_SPEED
    current_warp_amount = WARP_AMOUNT

    if mode == 'balatro':
        angle_table, radius_table = _spiral_tables # type: ignore
        render_balatro_frame(
            angle_table,
            radius_table,
            rgb_buffer,
            WIDTH,
            HEIGHT,
            frame_time,
            current_spin_speed,
            current_warp_amount
        )
    elif mode == 'plasma':
        render_plasma_frame(rgb_buffer, WIDTH, HEIGHT, frame_time & 0xFF)
    elif mode == 'fire':
        render_fire_frame(fire_buffer, rgb_buffer, WIDTH, HEIGHT, frame_time & 0xFF)
    elif mode == 'spiral':
        angle_table, radius_table = _spiral_tables # type: ignore
        render_spiral_frame(
            angle_table,
            radius_table,
            rgb_buffer,
            WIDTH,
            HEIGHT,
            frame_time & 0xFF,
            current_spin_speed
        )

@micropython.native
def _effect_loop():
    global _running

    assert driver is not None
    assert _rgb_buffer is not None
    assert _fire_buffer is not None

    rgb_buffer = _rgb_buffer
    fire_buffer = _fire_buffer
    frame_time = 0
    cycle_index = 0
    cycle_frame_count = 0

    while _running:
        mode = _effect_mode

        # Handle cycling mode
        if mode == 'cycle':
            # Switch effect every CYCLE_INTERVAL_MS (approx, based on frame count)
            # Assuming ~60fps, 10 seconds = ~600 frames
            if cycle_frame_count >= 600:
                cycle_frame_count = 0
                cycle_index = (cycle_index + 1) % len(_cycle_effects)
                # Re-init fire buffer when switching to fire
                if _cycle_effects[cycle_index] == 'fire':
                    _init_fire()
            current_effect = _cycle_effects[cycle_index]
            cycle_frame_count += 1
        else:
            current_effect = mode

        _render_effect(current_effect, rgb_buffer, fire_buffer, frame_time)

        driver.load_rgb888(rgb_buffer)
        driver.flip()
        frame_time += 1

    driver.clear()
    driver.flip()

def _start_effect(mode):
    global _running, _effect_mode

    if _running:
        _running = False
        sleep_ms(100)

    _ensure_buffers()
    if mode in ('balatro', 'spiral'):
        _ensure_spiral_tables()
    if mode == 'fire':
        _init_fire()

    _effect_mode = mode
    _running = True
    _thread.start_new_thread(_effect_loop, ())

def balatro():
    _init()
    _start_effect('balatro')
    print("Balatro started.")

def plasma():
    _init()
    _start_effect('plasma')
    print("Plasma started.")

def fire():
    _init()
    _start_effect('fire')
    print("Fire started.")

def spiral():
    _init()
    _start_effect('spiral')
    print("Spiral started.")

def stop():
    global _running
    if not _running:
        return
    _running = False
    sleep_ms(100)
    print("Stopped.")

def brightness(value: float | None = None):
    """Get or set display brightness (0.0 - 1.0)."""
    if driver is None:
        print("Driver not initialized. Start an effect first.")
        return
    if value is None:
        print(f"Brightness: {driver.brightness}")
    else:
        actual = driver.set_brightness(value)
        print(f"Brightness set to {actual}")

def blanking_time(nanoseconds: int | None = None):
    """Get or set blanking time in nanoseconds."""
    if driver is None:
        print("Driver not initialized. Start an effect first.")
        return
    if nanoseconds is None:
        print(f"Blanking time: {driver.blanking_time} ns")
    else:
        actual = driver.set_blanking_time(nanoseconds)
        print(f"Blanking time set to {actual} ns")

def gamma(value: gamma_module.SRGB | gamma_module.Power | None = ...): # type: ignore
    """Get or set display gamma correction (default SRGB).

    Usage:
        gamma()                        -- print current setting
        gamma(gamma_module.SRGB())     -- sRGB with linear region (default)
        gamma(gamma_module.Power(2.2)) -- simple power function
        gamma(None)                    -- no gamma correction
    """
    if driver is None:
        print("Driver not initialized. Start an effect first.")
        return
    if value is ...:
        current = driver.gamma
        if current is None:
            print("Gamma: None (no correction)")
        elif isinstance(current, gamma_module.SRGB):
            print("Gamma: SRGB")
        elif isinstance(current, gamma_module.Power):
            print(f"Gamma: Power({current.value})")
    else:
        driver.set_gamma(value)
        if value is None:
            print("Gamma set to None (no correction)")
        elif isinstance(value, gamma_module.SRGB):
            print("Gamma set to SRGB")
        elif isinstance(value, gamma_module.Power):
            print(f"Gamma set to Power({value.value})")

def refresh_rate(target_refresh_rate: float | None = None):
    """Get current refresh rate, or set a target refresh rate in Hz."""
    if driver is None:
        print("Driver not initialized. Start an effect first.")
        return
    if target_refresh_rate is None:
        print(f"Refresh rate: {driver.refresh_rate:.1f} Hz")
    else:
        actual = driver.set_target_refresh_rate(target_refresh_rate)
        print(f"Refresh rate set to {actual:.1f} Hz")

# Cycle settings
CYCLE_INTERVAL_MS = 10000
_cycle_effects = ['balatro', 'plasma', 'fire', 'spiral']

def cycle():
    global _running, _effect_mode
    _init()

    if _running:
        stop()

    _ensure_buffers()
    _ensure_spiral_tables()
    _init_fire()

    _effect_mode = 'cycle'
    _running = True
    _thread.start_new_thread(_effect_loop, ())
    print("Cycling effects every 10 seconds. Call stop() to end.")

def print_pinout():
    address_bit_count = bit_length(HEIGHT // 2 - 1)

    def create_address_pinout(address_index):
        return f'    {ord("A") + address_index:c}: GPIO {address_index + BASE_ADDRESS_PIN}'

    pinout = f"""Pinout:
    R1: GPIO {BASE_DATA_PIN}
    G1: GPIO {BASE_DATA_PIN + 1}
    B1: GPIO {BASE_DATA_PIN + 2}
    R2: GPIO {BASE_DATA_PIN + 3}
    G2: GPIO {BASE_DATA_PIN + 4}
    B2: GPIO {BASE_DATA_PIN + 5}
    CLK: GPIO {BASE_CLOCK_PIN}
    LAT: GPIO {BASE_CLOCK_PIN + 1}
    OE: GPIO {OUTPUT_ENABLE_PIN}
{'\n'.join(create_address_pinout(address_index) for address_index in range(address_bit_count))}
    """

    print(pinout)

print("=== HUB75 Interactive Demo ===")
print("Commands: print_pinout(), cycle(), balatro(), plasma(), fire(), spiral(), stop(), brightness(0.0-1.0), blanking_time(ns), gamma(n), refresh_rate(hz)")

cycle()
print_pinout()
