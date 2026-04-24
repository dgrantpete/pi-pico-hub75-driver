import micropython
import rp2
import machine
import uctypes
from . import native
from array import array
from .constants import COLOR_BIT_DEPTH
from micropython import const
import _thread
import re

from .row_addressing import Binary, ShiftRegister, Direct
from .gamma import SRGB, Power
from pio_types import *

_DEFAULT_PIO_INDEX = const(0)

_DMA_READ_ADDRESS_TRIGGER_INDEX = const(15)

_DMA_8BIT_TRANSFER_SIZE = const(0)
_DMA_16BIT_TRANSFER_SIZE = const(1)
_DMA_32BIT_TRANSFER_SIZE = const(2)

_DMA_UNPACED_TRANSFER_REQUEST = const(0x3F)

_DATA_STATE_MACHINE_OFFSET = const(0)
_ADDRESS_STATE_MACHINE_OFFSET = const(1)
_LATCH_SAFE_IRQ = const(0)
_LATCH_COMPLETE_IRQ = const(1)

# PIO register base addresses (same on RP2040 and RP2350)
_PIO_BASE_ADDRESSES = (
    const(0x50200000),  # PIO0
    const(0x50300000),  # PIO1
    const(0x50400000),  # PIO2 (RP2350 only)
)
_SM_CLKDIV_OFFSET = const(0x0C8)  # SM0_CLKDIV offset from PIO base
_SM_CLKDIV_STRIDE = const(0x18)   # Bytes between each SM's registers

_PIO_DEBUG_FLAGS_OFFSET = const(0x008)
_PIO_IRQ_OFFSET = const(0x030)
_PIO_IRQ_FORCE_OFFSET = const(0x034)

_PIO_TX_FLAG_BASE_INDEX = const(24)

_PIO_INDEX_EXPRESSION = re.compile(r'PIO\((\d)\)')

class _StateMachineSet:
    def __init__(
        self,
        *,
        data_state_machine: rp2.StateMachine,
        address_state_machine: rp2.StateMachine,
        address_update_cycles: int,
        bitplane_transition_extra_cycles: int,
    ):
        self.data_state_machine = data_state_machine
        self.address_state_machine = address_state_machine
        self.address_update_cycles = address_update_cycles
        self.bitplane_transition_extra_cycles = bitplane_transition_extra_cycles

class Hub75Driver:
    @micropython.native
    def __init__(
            self,
            *,
            row_addressing: Binary | ShiftRegister | Direct,
            shift_register_depth: int,
            pio: rp2.PIO | None = None,
            output_enable_pin: machine.Pin,
            base_data_pin: machine.Pin,
            base_clock_pin: machine.Pin,
            data_frequency: int = 20_000_000,
            brightness: float = 1.0,
            blanking_time: int = 0,
            gamma: SRGB | Power | None = SRGB(),
            target_refresh_rate: float = 120.0
        ):
        if isinstance(row_addressing, Binary):
            self._row_address_count = 1 << row_addressing.bit_count
        elif isinstance(row_addressing, ShiftRegister):
            self._row_address_count = row_addressing.depth
        elif isinstance(row_addressing, Direct):
            self._row_address_count = row_addressing.address_count
        else:
            raise TypeError(f"Unsupported row addressing type: {type(row_addressing)}")

        self._shift_register_depth = shift_register_depth
        self._data_frequency = data_frequency
        self._system_frequency = machine.freq()

        self._timing_buffer = array('I', [0] * (COLOR_BIT_DEPTH * 2))
        self._timing_buffer_pointer = array('I', [uctypes.addressof(self._timing_buffer)])

        self._gamma = gamma
        self._gamma_lut = self.__class__._create_gamma_lut(self._gamma)
        self._brightness = max(0.0, min(1.0, brightness))
        self._blanking_time = max(0, blanking_time)

        self._pio = pio if pio is not None else rp2.PIO(_DEFAULT_PIO_INDEX)
        self._pio_block_id = self.__class__._get_pio_index(self._pio)

        state_machine_set = self.__class__._create_state_machines(
            row_addressing=row_addressing,
            pio=self._pio,
            pio_block_id=self._pio_block_id,
            output_enable_pin=output_enable_pin,
            base_data_pin=base_data_pin,
            base_clock_pin=base_clock_pin,
            data_frequency=data_frequency,
            shift_register_depth=shift_register_depth,
            system_frequency=self._system_frequency
        )
        self._data_state_machine = state_machine_set.data_state_machine
        self._address_state_machine = state_machine_set.address_state_machine
        self._address_update_cycles = state_machine_set.address_update_cycles
        self._bitplane_transition_extra_cycles = state_machine_set.bitplane_transition_extra_cycles

        self.set_target_refresh_rate(target_refresh_rate)

        buffer_size = self.row_address_count * shift_register_depth * COLOR_BIT_DEPTH

        self._buffers = [
            bytearray(buffer_size),
            bytearray(buffer_size)
        ]

        self._active_buffer_index = 0

        self._active_buffer_address_pointer = array('I', [uctypes.addressof(self._active_buffer)])

        self._data_state_machine_offset = _DATA_STATE_MACHINE_OFFSET

        # Data path DMAs: pixel buffer -> data state machine
        self._data_buffer_dma = rp2.DMA()
        self._data_control_dma = rp2.DMA()

        self._data_buffer_dma.config(
            ctrl=self._data_buffer_dma.pack_ctrl(
                size=_DMA_32BIT_TRANSFER_SIZE,
                inc_read=True,
                inc_write=False,
                chain_to=self._data_control_dma.channel, # type: ignore
                treq_sel=self.__class__._get_pio_data_request_index(self._pio_block_id, self._data_state_machine_offset),
                irq_quiet=True
            ),
            write=self._data_state_machine,
            read=self._active_buffer,
            count=len(self._active_buffer) // 4 # divide by 4 since '_active_buffer' is in bytes with 32-bit transfers
        )

        self._data_control_dma.config(
            ctrl=self._data_control_dma.pack_ctrl(
                size=_DMA_32BIT_TRANSFER_SIZE,
                inc_read=False,
                inc_write=False,
                treq_sel=_DMA_UNPACED_TRANSFER_REQUEST
            ),
            count=1,
            read=self._active_buffer_address_pointer,
            write=self._data_buffer_dma.registers[_DMA_READ_ADDRESS_TRIGGER_INDEX:_DMA_READ_ADDRESS_TRIGGER_INDEX+1], # type: ignore
            trigger=True
        )

        # Address path DMAs: timing buffer -> address state machine
        self._address_timing_dma = rp2.DMA()
        self._address_control_dma = rp2.DMA()

        self._address_timing_dma.config(
            ctrl=self._address_timing_dma.pack_ctrl(
                size=_DMA_32BIT_TRANSFER_SIZE,
                inc_read=True,
                inc_write=False,
                chain_to=self._address_control_dma.channel, # type: ignore
                treq_sel=self.__class__._get_pio_data_request_index(self._pio_block_id, _ADDRESS_STATE_MACHINE_OFFSET),
                irq_quiet=True
            ),
            write=self._address_state_machine,
            read=self._timing_buffer,
            count=COLOR_BIT_DEPTH * 2
        )

        self._address_control_dma.config(
            ctrl=self._address_control_dma.pack_ctrl(
                size=_DMA_32BIT_TRANSFER_SIZE,
                inc_read=False,
                inc_write=False,
                treq_sel=_DMA_UNPACED_TRANSFER_REQUEST
            ),
            count=1,
            read=self._timing_buffer_pointer,
            write=self._address_timing_dma.registers[_DMA_READ_ADDRESS_TRIGGER_INDEX:_DMA_READ_ADDRESS_TRIGGER_INDEX+1], # type: ignore
            trigger=True
        )

        self._data_state_machine.active(1)
        self._address_state_machine.active(1)

    @micropython.native
    def deinit(self):
        shutdown_lock = _thread.allocate_lock()
        shutdown_lock.acquire()

        def on_data_dma_complete(_):
            shutdown_lock.release()

        self._data_buffer_dma.irq(handler=on_data_dma_complete, hard=True)

        # Cut off the ping-ponged DMAs to stop the loop
        # Graceful stop by cutting chain rather than forcefully stopping means
        # the DMAs are in a clean state when they are done
        self._data_buffer_dma.config(
            ctrl=self._data_buffer_dma.pack_ctrl(
                size=_DMA_32BIT_TRANSFER_SIZE,
                inc_read=True,
                inc_write=False,
                chain_to=self._data_buffer_dma.channel, # type: ignore
                treq_sel=self.__class__._get_pio_data_request_index(self._pio_block_id, self._data_state_machine_offset),
                irq_quiet=False # Atomically enable IRQ to fire only when chain broken
            ),
            write=self._data_state_machine,
            read=self._active_buffer,
            count=len(self._active_buffer) // 4
        )

        # Wait until we're sure the DMA has finished (and is no longer triggering the data DMA)
        shutdown_lock.acquire()

        # Close control DMAs first so they can't re-trigger buffer/timing DMAs
        self._data_control_dma.close()
        self._address_control_dma.close()
        self._data_buffer_dma.close()
        self._address_timing_dma.close()

        pio_base = _PIO_BASE_ADDRESSES[self._pio_block_id]

        # Force-set both handshake IRQs to unblock any SM stuck on a wait instruction.
        # After DMAs are closed, the address SM may stall on 'out' (empty FIFO),
        # which prevents it from ever firing _LATCH_SAFE_IRQ. If the data SM is
        # blocked on 'wait(1, irq, _LATCH_SAFE_IRQ)', it will never reach an 'out'
        # instruction and the TX stall flag will never be set.
        machine.mem32[pio_base + _PIO_IRQ_FORCE_OFFSET] = (1 << _LATCH_SAFE_IRQ) | (1 << _LATCH_COMPLETE_IRQ)

        tx_flag_index = _PIO_TX_FLAG_BASE_INDEX + self._data_state_machine_offset
        tx_bit_mask = 1 << tx_flag_index

        # Clear stall flag so we detect a fresh stall
        machine.mem32[pio_base + _PIO_DEBUG_FLAGS_OFFSET] = tx_bit_mask

        while (machine.mem32[pio_base + _PIO_DEBUG_FLAGS_OFFSET] & tx_bit_mask) == 0:
            # Wait until data state machine is stalled (no more data to pull from DMA)
            machine.idle()

        # Deactivate the state machines
        self._data_state_machine.active(0)
        self._address_state_machine.active(0)

        # Clear any leftover handshake IRQ flags so the next init starts with clean state.
        # The force-set above (and normal SM execution) can leave flags set, which would
        # cause the data SM to skip its first wait on the next init, offsetting rows by 1.
        machine.mem32[pio_base + _PIO_IRQ_OFFSET] = (1 << _LATCH_SAFE_IRQ) | (1 << _LATCH_COMPLETE_IRQ)

        self._pio.remove_program()

    @micropython.native
    def load_rgb888(self, rgb888_data: memoryview | bytes | bytearray):
        native.load_rgb888(rgb888_data, self._inactive_buffer, self._gamma_lut)

    @micropython.native
    def load_rgb565(self, rgb565_data: memoryview | bytes | bytearray):
        native.load_rgb565(rgb565_data, self._inactive_buffer, self._gamma_lut)

    @micropython.native
    def clear(self):
        native.clear(self._inactive_buffer)

    @micropython.native
    def flip(self):
        self._active_buffer_index = 1 - self._active_buffer_index
        self._active_buffer_address_pointer[0] = uctypes.addressof(self._active_buffer)

    @micropython.native
    def set_frequency(self, data_frequency: int) -> int:
        self._data_frequency = data_frequency
        system_frequency = self._system_frequency
        pio_base = _PIO_BASE_ADDRESSES[self._pio_block_id]

        clkdiv_address = pio_base + _SM_CLKDIV_OFFSET + (_DATA_STATE_MACHINE_OFFSET * _SM_CLKDIV_STRIDE)
        divider = system_frequency / (data_frequency * 2)
        integer_part = int(divider)
        fractional_part = int((divider - integer_part) * 256)
        machine.mem32[clkdiv_address] = (integer_part << 16) | (fractional_part << 8)
        return self._data_frequency

    @property
    @micropython.native
    def row_address_count(self) -> int:
        return self._row_address_count
    
    @property
    @micropython.native
    def shift_register_depth(self) -> int:
        return self._shift_register_depth

    @property
    @micropython.native
    def data_frequency(self) -> int:
        return self._data_frequency

    @property
    @micropython.native
    def system_frequency(self) -> int:
        return self._system_frequency

    @micropython.native
    def sync_system_frequency(self) -> int:
        self._system_frequency = machine.freq()
        self.set_frequency(self._data_frequency)
        self._update_timing_buffer(self._base_cycles, self._brightness, self._blanking_time, self._system_frequency)
        return self._system_frequency

    @property
    @micropython.native
    def brightness(self) -> float:
        return self._brightness
    
    @property
    @micropython.native
    def blanking_time(self) -> int:
        return self._blanking_time

    @property
    @micropython.native
    def refresh_rate(self) -> float:
        return self._estimate_refresh_rate(self._base_cycles, self._brightness, self._blanking_time, self._system_frequency)

    @micropython.native
    def set_brightness(self, brightness: float) -> float:
        self._brightness = max(0.0, min(1.0, brightness))
        self._update_timing_buffer(self._base_cycles, self._brightness, self._blanking_time, self._system_frequency)
        return self._brightness

    @micropython.native
    def set_blanking_time(self, nanoseconds: int) -> int:
        self._blanking_time = max(0, nanoseconds)
        self._update_timing_buffer(self._base_cycles, self._brightness, self._blanking_time, self._system_frequency)
        return self._blanking_time

    @property
    @micropython.native
    def gamma(self) -> SRGB | Power | None:
        return self._gamma

    @micropython.native
    def set_gamma(self, gamma: SRGB | Power | None) -> SRGB | Power | None:
        self._gamma = gamma
        self._gamma_lut = Hub75Driver._create_gamma_lut(self._gamma)
        return self._gamma

    @staticmethod
    @micropython.native
    def _create_gamma_lut(gamma: SRGB | Power | None) -> bytearray:
        max_value = (1 << COLOR_BIT_DEPTH) - 1
        lut = bytearray(1 << COLOR_BIT_DEPTH)
        if gamma is None:
            for i in range(1 << COLOR_BIT_DEPTH):
                lut[i] = i
        elif isinstance(gamma, SRGB):
            inv_max = 1.0 / max_value
            for i in range(1 << COLOR_BIT_DEPTH):
                x = i * inv_max
                if x <= 0.04045:
                    linear = x / 12.92
                else:
                    linear = ((x + 0.055) / 1.055) ** 2.4
                lut[i] = round(max_value * linear)
        elif isinstance(gamma, Power):
            if gamma.value == 1.0:
                for i in range(1 << COLOR_BIT_DEPTH):
                    lut[i] = i
            else:
                inv_max = 1.0 / max_value
                for i in range(1 << COLOR_BIT_DEPTH):
                    lut[i] = round(max_value * ((i * inv_max) ** gamma.value))
        else:
            raise TypeError(f"Unsupported gamma type: {type(gamma)}")
        return lut

    @micropython.native
    def _update_timing_buffer(self, base_cycles: int, brightness: float, blanking_time: int, system_frequency: int):
        blanking_cycles = (blanking_time * system_frequency) // 1_000_000_000

        for bitframe_index in range(COLOR_BIT_DEPTH):
            # Represents the total on/off delay cycles that contribute to brightness ratio (not including blanking time)
            brightness_cycle = base_cycles << bitframe_index

            on_cycles = max(
                int(brightness * brightness_cycle),
                0
            )

            off_cycles = max(
                # Off delay value is halved because delay occurs twice per bitframe (once before enable and once after to prevent ghosting)
                ((brightness_cycle - on_cycles) // 2) + blanking_cycles,
                0
            )

            off_timing_index = bitframe_index * 2
            self._timing_buffer[off_timing_index] = off_cycles
            self._timing_buffer[off_timing_index + 1] = on_cycles

    @micropython.native
    def _estimate_refresh_rate(self, base_cycles: int, brightness: float, blanking_time: int, system_frequency: int) -> float:
        # PIO cycle overhead constants (derived from cycle-counting the assembly programs)
        # Address SM: non-delay instructions per row (outside of increment_address())
        # mov(y,isr) + loop_exit + mov(y,osr) + loop_exit + mov(y,isr) + loop_exit + irq
        ADDRESS_DISPLAY_OVERHEAD_CYCLES = const(7)
        # Address SM sequential handshake cycles per row: increment_address() + wait(minimum 1 cycle)
        address_handshake_overhead_cycles = self._address_update_cycles + 1
        # Data SM sequential handshake cycles per row: wait(LATCH_SAFE) + irq(LATCH_COMPLETE)
        DATA_HANDSHAKE_OVERHEAD_CYCLES = const(2)
        # Data SM per-row setup before the pixel clocking loop: mov(x, y)
        DATA_RELOAD_OVERHEAD_CYCLES = const(1)
        # Data SM per-pixel in the clocking loop: out(pins, 8) + jmp(x_dec)
        DATA_CYCLES_PER_PIXEL = const(2)
        # Address SM extra cycles per bitplane transition (not per row):
        # partial increment_address() (until jmp to increment_bitplane is taken)
        # + out(null, 32) + increment_bitplane() + out(isr, 32). The subsequent
        # wrap back into increment_address() and row display are counted as a normal row.
        bitplane_transition_extra_cycles = self._bitplane_transition_extra_cycles

        row_count = self.row_address_count

        # Data SM runs at (data_frequency * 2), so each data SM cycle takes this many system cycles
        data_clock_ratio = system_frequency / (self._data_frequency * 2)

        # Data SM transfer time per row, converted to system clock cycles
        data_transfer_cycles = (
            DATA_RELOAD_OVERHEAD_CYCLES + DATA_CYCLES_PER_PIXEL * self._shift_register_depth
        ) * data_clock_ratio

        # Handshake overhead per row in system clock cycles
        # Address SM contributes fixed cycles; Data SM contributes cycles scaled by clock ratio
        handshake_cycles = (
            address_handshake_overhead_cycles
            + DATA_HANDSHAKE_OVERHEAD_CYCLES * data_clock_ratio
        )

        blanking_cycles = (blanking_time * system_frequency) // 1_000_000_000
        total_frame_cycles = 0.0

        for bitplane_index in range(COLOR_BIT_DEPTH):
            brightness_cycle = base_cycles << bitplane_index
            on_cycles = max(int(brightness * brightness_cycle), 0)
            off_cycles = max(((brightness_cycle - on_cycles) // 2) + blanking_cycles, 0)

            # Address SM display time per row for this bitplane
            address_display_cycles = (
                on_cycles + 2 * off_cycles + ADDRESS_DISPLAY_OVERHEAD_CYCLES
            )

            # The address SM and data SM work concurrently after the handshake;
            # the row time is gated by whichever is slower
            row_cycles = max(address_display_cycles, data_transfer_cycles) + handshake_cycles

            total_frame_cycles += row_count * row_cycles

        total_frame_cycles += bitplane_transition_extra_cycles * COLOR_BIT_DEPTH

        if total_frame_cycles <= 0:
            return 0.0

        return system_frequency / total_frame_cycles

    @micropython.native
    def set_target_refresh_rate(self, target_refresh_rate: float) -> float:
        brightness = self._brightness
        blanking_time = self._blanking_time
        system_frequency = self._system_frequency

        estimate = self._estimate_refresh_rate

        # Check if target is achievable at base_cycles=1 (maximum refresh rate)
        base_cycles = 1
        maximum_refresh_rate = estimate(base_cycles, brightness, blanking_time, system_frequency)

        if target_refresh_rate >= maximum_refresh_rate:
            self._base_cycles = base_cycles
            self._update_timing_buffer(base_cycles, brightness, blanking_time, system_frequency)
            return maximum_refresh_rate

        # Estimate upper bound for binary search: approximate frame time when display-limited
        # frame_time is about rows * base_cycles * (2^n - 1), solve for base_cycles
        bitplane_sum = (1 << COLOR_BIT_DEPTH) - 1
        estimated_base_cycles = system_frequency // int(
            target_refresh_rate * self.row_address_count * bitplane_sum
        )
        search_upper_bound = max(estimated_base_cycles * 2, 2)

        # Verify the upper bound actually produces a rate below target (expand if not)
        while estimate(search_upper_bound, brightness, blanking_time, system_frequency) > target_refresh_rate:
            search_upper_bound *= 2

        # Binary search: find the smallest base_cycles where refresh rate <= target
        search_lower_bound = 1
        while search_lower_bound < search_upper_bound:
            search_midpoint = (search_lower_bound + search_upper_bound) // 2

            midpoint_refresh_rate = estimate(search_midpoint, brightness, blanking_time, system_frequency)

            if midpoint_refresh_rate > target_refresh_rate:
                search_lower_bound = search_midpoint + 1
            else:
                search_upper_bound = search_midpoint

        # Compare candidate with candidate-1 to find the closest to the target
        base_cycles = search_lower_bound
        rate_at_candidate = estimate(base_cycles, brightness, blanking_time, system_frequency)

        if base_cycles > 1:
            rate_above_target = estimate(base_cycles - 1, brightness, blanking_time, system_frequency)

            # Pick whichever is arithmetically closer to the target
            distance_below = target_refresh_rate - rate_at_candidate
            distance_above = rate_above_target - target_refresh_rate

            if distance_above <= distance_below:
                base_cycles = base_cycles - 1

        # Commit the final result
        self._base_cycles = base_cycles
        self._update_timing_buffer(base_cycles, brightness, blanking_time, system_frequency)
        return estimate(base_cycles, brightness, blanking_time, system_frequency)

    @staticmethod
    @micropython.native
    def _get_pio_index(pio: rp2.PIO) -> int:
        # Micropython API doesn't expose PIO index as a direct integer, so we need to (unfortunately) extract it from its string representation
        match = _PIO_INDEX_EXPRESSION.match(repr(pio))

        if not match:
            raise ValueError(f"Could not determine PIO index: '{pio!r}'")

        return int(match.group(1))

    @staticmethod
    @micropython.native
    def _get_absolute_state_machine_id(pio_block_id: int, state_machine_offset: int) -> int:
        return pio_block_id * 4 + state_machine_offset

    @staticmethod
    @micropython.native
    def _get_pio_data_request_index(pio_block_id: int, state_machine_id: int) -> int:
        return (pio_block_id << 3) | (state_machine_id & 0b11)

    @property
    @micropython.native
    def _active_buffer(self) -> bytearray:
        return self._buffers[self._active_buffer_index]
    
    @property
    @micropython.native
    def _inactive_buffer(self) -> bytearray:
        return self._buffers[1 - self._active_buffer_index]

    @staticmethod
    @micropython.native
    def _create_state_machines(
        *,
        row_addressing: Binary | ShiftRegister | Direct,
        pio: rp2.PIO,
        pio_block_id: int,
        output_enable_pin: machine.Pin,
        base_data_pin: machine.Pin,
        base_clock_pin: machine.Pin,
        data_frequency: int,
        shift_register_depth: int,
        system_frequency: int
    ) -> _StateMachineSet:
        data_state_machine_id = Hub75Driver._get_absolute_state_machine_id(
            pio_block_id, _DATA_STATE_MACHINE_OFFSET
        )
        address_state_machine_id = Hub75Driver._get_absolute_state_machine_id(
            pio_block_id, _ADDRESS_STATE_MACHINE_OFFSET
        )

        if isinstance(row_addressing, Binary):
            address_decorator = rp2.asm_pio(
                sideset_init=rp2.PIO.OUT_HIGH,
                out_init=[rp2.PIO.OUT_LOW] * row_addressing.bit_count,
                out_shiftdir=rp2.PIO.SHIFT_RIGHT,
                autopull=True,
                pull_thresh=32
            )

            # increment_address normal path: jmp(x_dec) taken + mov(pins, invert(x))
            address_update_cycles = 2
            # Partial increment_address (2: jmp(x_dec) not taken + jmp to increment_bitplane)
            # + out(null, 32) + increment_bitplane() (1) + out(isr, 32)
            bitplane_transition_extra_cycles = 5

            def increment_bitplane():
                set(x, (0b1 << row_addressing.bit_count)).side(OE_DEASSERTED)

            def increment_address():
                jmp(x_dec, "write_address")            .side(OE_DEASSERTED)
                jmp("increment_bitplane")              .side(OE_DEASSERTED)
                label("write_address")
                # We invert the bits here so it counts up from 0 to the highest address
                # (even though the x register itself counts down from the highest address to 0)
                mov(pins, invert(x)).side(OE_DEASSERTED)

        elif isinstance(row_addressing, ShiftRegister):
            address_decorator = rp2.asm_pio(
                sideset_init=rp2.PIO.OUT_HIGH,
                out_init=rp2.PIO.OUT_LOW,
                set_init=rp2.PIO.OUT_LOW,
                out_shiftdir=rp2.PIO.SHIFT_RIGHT,
                autopull=True,
                pull_thresh=32
            )

            shift_register_frequency = row_addressing.clock_frequency if row_addressing.clock_frequency is not None else data_frequency
            max_delay = 15  # 4 delay bits available (5-bit field shared with 1 sideset pin)
            half_period_cycles = -(-system_frequency // (2 * shift_register_frequency))
            shift_register_delay = half_period_cycles - 1

            if shift_register_delay > max_delay:
                minimum_frequency = system_frequency // (2 * (1 + max_delay))
                if row_addressing.clock_frequency is None:
                    raise ValueError(
                        f"The shift register clock frequency cannot be reduced to the inherited data frequency "
                        f"({data_frequency} Hz). The minimum achievable shift register clock frequency is "
                        f"{minimum_frequency} Hz. Set clock_frequency explicitly on ShiftRegister to use a "
                        f"different target."
                    )
                else:
                    raise ValueError(
                        f"The specified shift register clock frequency ({row_addressing.clock_frequency} Hz) "
                        f"is below the minimum achievable shift register clock frequency of {minimum_frequency} Hz."
                    )

            shift_register_delay = max(0, shift_register_delay)

            # increment_address normal path: jmp(x_dec) taken (1, no delay)
            # + set(pins, 1)[d] + set(pins, 0)[d] + mov(pins, null)[d]
            address_update_cycles = 1 + 3 * (1 + shift_register_delay)
            # Partial increment_address (2: jmp(x_dec) not taken + jmp to increment_bitplane; no delays)
            # + out(null, 32) + increment_bitplane() (2 + d) + out(isr, 32)
            bitplane_transition_extra_cycles = 6 + shift_register_delay

            def increment_bitplane():
                set(x, row_addressing.depth).side(OE_DEASSERTED)
                mov(pins, invert(null)).side(OE_DEASSERTED) [shift_register_delay]

            def increment_address():
                jmp(x_dec, "write_address").side(OE_DEASSERTED)
                jmp("increment_bitplane").side(OE_DEASSERTED)
                label("write_address")
                set(pins, 1).side(OE_DEASSERTED) [shift_register_delay]
                set(pins, 0).side(OE_DEASSERTED) [shift_register_delay]

                # If data pin was previously high, which is only possible after bitplane initialization,
                # a '1' is now shifted into the shift register. If it was already low, a '0' is shifted in and
                # will remain like that for the rest of the bitframe
                mov(pins, null).side(OE_DEASSERTED) [shift_register_delay]
        elif isinstance(row_addressing, Direct):
            address_decorator = rp2.asm_pio(
                sideset_init=rp2.PIO.OUT_HIGH,
                out_init=[rp2.PIO.OUT_LOW] * row_addressing.address_count,
                out_shiftdir=rp2.PIO.SHIFT_RIGHT,
                in_shiftdir=rp2.PIO.SHIFT_RIGHT,
                autopull=True,
                pull_thresh=32
            )

            # increment_address normal path: mov(y, osr) + mov(osr, x) + out(null, 1)
            # + mov(x, osr) + mov(osr, y) + jmp(not_x, ...) not taken + mov(pins, x)
            address_update_cycles = 7
            # Partial increment_address (6: everything up through jmp(not_x, ...) taken)
            # + out(null, 32) + increment_bitplane() (4) + out(isr, 32)
            bitplane_transition_extra_cycles = 12

            def increment_bitplane():
                set(x, 0b1).side(OE_DEASSERTED)
                mov(isr, null).side(OE_DEASSERTED)
                in_(x, 32 - row_addressing.address_count).side(OE_DEASSERTED)
                mov(x, isr).side(OE_DEASSERTED)

            def increment_address():
                # y register is used temporarily to store the OSR contents (y isn't used until after, so its safe)
                mov(y, osr).side(OE_DEASSERTED)

                # Next instructions are equivalent to 'x >>= 1'
                mov(osr, x).side(OE_DEASSERTED)
                out(null, 1).side(OE_DEASSERTED)
                mov(x, osr).side(OE_DEASSERTED)

                # Restore OSR's original value
                mov(osr, y).side(OE_DEASSERTED)

                # A value of 0 means we've shifted the single '1' off the end and should start the next bitplane
                jmp(not_x, "increment_bitplane").side(OE_DEASSERTED)

                mov(pins, x).side(OE_DEASSERTED)
        else:
            raise TypeError(f"Unsupported row addressing type: {type(row_addressing)}")

        OE_ASSERTED = const(0b0)
        OE_DEASSERTED = const(0b1)

        @address_decorator
        def address_program():
            # We don't want to discard the first timing word
            # We jump over the instruction that would do so
            jmp("initialize")                      .side(OE_DEASSERTED)
            label("increment_bitplane")
            # Discard data from OSR to hold next delays
            out(null, 32)                          .side(OE_DEASSERTED)
            label("initialize")
            increment_bitplane()
            # After this, ISR contains the 'off' delay from the first word, OSR contains the 'on' delay from the second word (autopulled)
            out(isr, 32)                           .side(OE_DEASSERTED)
            wrap_target()
            increment_address()
            irq(_LATCH_SAFE_IRQ)                   .side(OE_DEASSERTED)
            wait(1, irq, _LATCH_COMPLETE_IRQ)      .side(OE_DEASSERTED)
            mov(y, isr)                            .side(OE_DEASSERTED)
            label("off_delay_before_enable")
            jmp(y_dec, "off_delay_before_enable")  .side(OE_DEASSERTED)
            mov(y, osr)                            .side(OE_DEASSERTED)
            label("on_delay")
            jmp(y_dec, "on_delay")                 .side(OE_ASSERTED)
            mov(y, isr)                            .side(OE_DEASSERTED)
            label("off_delay_after_disable")
            jmp(y_dec, "off_delay_after_disable")  .side(OE_DEASSERTED)
            wrap()

        CLOCK_ASSERTED = const(0b01)
        LATCH_ASSERTED = const(0b10)
        BOTH_DEASSERTED = const(0b00)

        @rp2.asm_pio(
            sideset_init=[rp2.PIO.OUT_LOW] * 2,
            out_init=[rp2.PIO.OUT_LOW] * 6,
            out_shiftdir=rp2.PIO.SHIFT_RIGHT,
            autopull=True,
            pull_thresh=32
        )
        def data_program():
            out(y, 32)                          .side(BOTH_DEASSERTED)
            wrap_target()
            mov(x, y)                           .side(BOTH_DEASSERTED)
            label("write_data")
            out(pins, 8)                        .side(BOTH_DEASSERTED)
            jmp(x_dec, "write_data")            .side(CLOCK_ASSERTED)
            wait(1, irq, _LATCH_SAFE_IRQ)       .side(BOTH_DEASSERTED)
            # The latch is triggered on the rising edge, so we can safely say that it has been latched
            # for the IRQ even if the latch hasn't yet been deasserted
            irq(_LATCH_COMPLETE_IRQ)            .side(LATCH_ASSERTED)
            wrap()

        # Clear ALL programs in this PIO so we're starting from a blank slate
        pio.remove_program()

        data_state_machine = rp2.StateMachine(
            data_state_machine_id,
            data_program,
            out_base=base_data_pin,
            sideset_base=base_clock_pin,
            freq=data_frequency * 2 # times 2 since each clock cycle has a rising and falling edge
        )

        # Seed data state machine with number of bits to clock out for each address
        data_state_machine.put(shift_register_depth - 1)

        if isinstance(row_addressing, Binary):
            address_state_machine = rp2.StateMachine(
                address_state_machine_id,
                address_program,
                out_base=row_addressing.base_pin,
                sideset_base=output_enable_pin
            )
        elif isinstance(row_addressing, ShiftRegister):
            address_state_machine = rp2.StateMachine(
                address_state_machine_id,
                address_program,
                set_base=row_addressing.clock_pin,
                out_base=row_addressing.data_pin,
                sideset_base=output_enable_pin
            )
        elif isinstance(row_addressing, Direct):
            address_state_machine = rp2.StateMachine(
                address_state_machine_id,
                address_program,
                out_base=row_addressing.base_pin,
                sideset_base=output_enable_pin
            )

        return _StateMachineSet(
            data_state_machine=data_state_machine,
            address_state_machine=address_state_machine,
            address_update_cycles=address_update_cycles,
            bitplane_transition_extra_cycles=bitplane_transition_extra_cycles
        )
    