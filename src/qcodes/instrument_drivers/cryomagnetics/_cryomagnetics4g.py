from __future__ import annotations

import time
from dataclasses import dataclass
from string import ascii_letters
from typing import Any

from pyvisa import VisaIOError

from qcodes.instrument import VisaInstrument
from qcodes.validators import Enum, Numbers


@dataclass
class CryomagneticsFailureConditions:
    quench_condition_present: bool = False
    power_module_failure: bool = False

    def can_start_ramping(self) -> bool:
        required_checks = [
            "quench_condition_present",
            "power_module_failure",
        ]
        return all(not getattr(self, field) for field in required_checks)


class Cryomagnetics4GException(Exception):
    pass


class Cryomagnetics4GWarning(Warning):
    pass


class CryomagneticsModel4G(VisaInstrument):
    """
    Driver for the Cryomagnetics Model 4G superconducting magnet power supply.

    This driver provides an interface to control and communicate with the Cryomagnetics Model 4G
    superconducting magnet power supply using the VISA protocol. It allows setting and reading
    the magnetic field, ramp rate, and various other parameters of the instrument.

    Args:
        name: The name of the instrument instance.
        address: The VISA resource name of the instrument.
        max_current_limits: A dictionary specifying the maximum
            current limits and rates for each range. The keys are the range indices, and the values
            are tuples containing the upper current limit and maximum rate for that range.
        coil_constant: The coil constant of the magnet in Tesla per Amp.
    """


    def __init__(
        self,
        name: str,
        address: str,
        max_current_limits: dict[int, tuple[float, float]],
        coil_constant: float,
        terminator: str = "\n",
        **kwargs: Any,
    ):
        super().__init__(name, address, terminator=terminator, **kwargs)

        self.coil_constant = coil_constant
        self.max_current_limits = max_current_limits

        # Initialize rate manager based on hypothetical hardware specific limits
        self._initialize_max_current_limits()

        # Adding parameters
        self.add_parameter(
            name="units",
            set_cmd="UNITS {}",
            get_cmd="UNITS?",
            get_parser=str,
            vals=Enum("A", "kG", "T"),
            docstring="Field Units",
        )

        self.add_parameter(
            "ramping_state_check_interval",
            initial_value=0.05,
            unit="s",
            vals=Numbers(0, 10),
            set_cmd=None,
        )

        self.add_parameter(
            name="field",
            unit="T",
            set_cmd=self.set_field,
            get_cmd=self.get_field,
            get_parser=float,
            vals=Numbers(-9.001, 9.001),
            docstring="Magnetic Field in Tesla",
        )

        self.add_parameter(
            name="rate",
            unit="T/min",
            get_cmd=self._get_rate,
            set_cmd=self._set_rate,
            get_parser=float,
            docstring="Rate for magnetic field T/min",
        )

        self.add_parameter(
            name="Vmag",
            unit="V",
            get_cmd=self._get_vmag,
            get_parser=float,
            vals=Numbers(-10, 10),
            docstring="Magnet sense voltage",
        )

        self.add_parameter(
            name="Vout",
            unit="V",
            get_cmd=self._get_vout,
            get_parser=float,
            vals=Numbers(-12.8, 12.8),
            docstring="Magnet output voltage",
        )

        self.add_parameter(
            name="Iout",
            unit="A",
            get_cmd=self._get_iout,
            get_parser=float,
            docstring="Magnet output field/current",
        )

        # Set to remote mode
        self.operating_mode()
        #  Set units to tesla by default
        self.units("T")
        self.connect_message()

    def _get_vmag(self) -> float:
        """
        Get Vmag (magnet sense voltage).
        """
        output = self.ask("VMAG?")
        letters = str.maketrans({c: None for c in ascii_letters})
        output = output.translate(letters)
        return float(output)

    def _get_vout(self) -> float:
        """
        Get Vout (magnet output voltage).
        """
        output = self.ask("VOUT?")
        letters = str.maketrans({c: None for c in ascii_letters})
        output = output.translate(letters)
        return float(output)

    def _get_iout(self) -> float:
        """
        Get Iout (magnet output field/current).
        """
        output = self.ask("IOUT?")
        letters = str.maketrans({c: None for c in ascii_letters})
        output = output.translate(letters)
        return float(output)

    def quenched_state_reset(self) -> None:
        """
        Resets the device's quenched state.
        """
        self.write("QRESET")

    def operating_mode(self, remote: bool = True) -> None:
        """
        Sets the device's operating mode to either remote or local.

        Args:
            remote: If True, sets to remote mode, otherwise sets to local mode.
        """
        if remote:
            self.write("REMOTE")
        else:
            self.write("LOCAL")

    def zero_current(self) -> None:
        """
        Sets the device current to zero.
        """
        self.write("SWEEP ZERO")

    def reset(self) -> None:
        """
        Resets the device to its default settings.
        """
        self.write("*RST")

    def check_failure_conditions(self) -> CryomagneticsFailureConditions:
        """
        Retrieves the current failure conditions of the magnet power supply.

        Returns:
            CryomagneticsFailureConditions: An object representing the current failure conditions of the magnet power supply.

        Raises:
            Cryomagnetics4GException: If the magnet power supply is in a state that prevents ramping, such as quench condition
                                       or power module failure.

        The failure conditions are determined by querying the status byte (`*STB?`) of the instrument. The status byte is
        interpreted as follows:
        - Bit 2: Quench condition present
        - Bit 3: Power module failure

        If the magnet power supply is in a state that prevents ramping (quench condition or power module failure),
        an exception is raised with an appropriate error message. The error message is also logged using the instrument's
        logger.

        If the magnet power supply is not experiencing any failure conditions, a CryomagneticsFailureConditions object is returned,
        representing the current failure state of the magnet power supply.
        """
        status_byte = int(self.ask("*STB?"))

        operating_state = CryomagneticsFailureConditions(
            quench_condition_present=bool(status_byte & 4),
            power_module_failure=bool(status_byte & 8),
        )

        if operating_state.quench_condition_present:
            error_message = "Cannot ramp due to quench condition."
            self.log.error(error_message)
            raise Cryomagnetics4GException(error_message)

        if operating_state.power_module_failure:
            error_message = "Cannot ramp due to power module failure."
            self.log.error(error_message)
            raise Cryomagnetics4GException(error_message)

        return operating_state

    def set_field(self, field_setpoint: float, block: bool = True) -> None:
        """
        Sets the magnetic field strength in Tesla using ULIM, LLIM, and SWEEP commands.

        Args:
            field_setpoint: The desired magnetic field strength in Tesla.
            block: If True, the method will block until the field reaches the setpoint.

        Raises:
            Cryo4GException: If the power supply is not in a state where it can start ramping.
        """
        # Convert field setpoint to kG for the instrument
        field_setpoint_kg = field_setpoint * 10
        # Determine sweep direction based on setpoint and current field
        current_field = self.get_field()

        self.log.debug(f"Current field: {current_field}, Setpoint: {field_setpoint_kg}")

        if abs(field_setpoint_kg - current_field) < 1e-4:
            # Already at the setpoint, no need to sweep
            self.log.info(f"Magnetic field is already set to {field_setpoint}T")
            return

        # Check if we can start ramping
        try:
            state = self.check_failure_conditions()
        except Cryomagnetics4GException as e:
            self.log.error(f"Cannot set field: {e}")  # Log the specific error
            return

        if state.can_start_ramping():
            if field_setpoint_kg < current_field:
                sweep_direction = "DOWN"
                self.write(f"LLIM {field_setpoint_kg}")
            else:
                sweep_direction = "UP"
                self.write(f"ULIM {field_setpoint_kg}")

            self.log.debug(f"Sweeping {sweep_direction} to {field_setpoint_kg}")

            self.write(f"SWEEP {sweep_direction}")

            # Check if we want to block
            if not block:
                self.log.warning("Magnetic field is ramping but not currently blocked!")
                return

            # Otherwise, wait until the field reaches the setpoint
            self.log.debug(
                f"Starting blocking ramp of {self.name} to {field_setpoint} T"
            )
            mag = self.field()
            while abs(field_setpoint - mag) > 0.002:
                time.sleep(5)
                mag = self.field()
            self.log.debug("Finished blocking ramp")

        self.write("SWEEP PAUSE")

    def get_field(self) -> float:
        """
        Get field value

        Args:
            None
        """
        output = self.ask("IMAG?")
        letters = str.maketrans({c: None for c in ascii_letters})
        output = output.translate(letters)
        if self.units() == "T":
            return float(output) / 10  # convert to Tesla
        return float(output)

    def _sleep(self, t: float) -> None:
        """
        Sleep for a number of seconds t. If we are or using
        the PyVISA 'sim' backend, omit this
        """

        simmode = getattr(self, "visabackend", False) == "sim"

        if simmode:
            return
        else:
            time.sleep(t)


    def _get_rate(self) -> float:
        """
        Get the current ramp rate in Tesla per minute.
        """
        # Get the rate from the instrument in Amps per second
        rate_amps_per_sec = float(self.ask("RATE?"))
        # Convert to Tesla per minute
        rate_tesla_per_min = rate_amps_per_sec * 60 / self.coil_constant
        return rate_tesla_per_min

    def _set_rate(self, rate_tesla_per_min: float) -> None:
        """
        Set the ramp rate in Tesla per minute.
        """
        # Convert from Tesla per minute to Amps per second
        rate_amps_per_sec = rate_tesla_per_min * self.coil_constant / 60
        # Find the appropriate range and set the rate
        current_field = self.get_field()  # Get current field in Tesla
        current_in_amps = current_field * self.coil_constant  # Convert to Amps

        # (Implement a  more efficient lookup method here if needed)
        for range_index, (upper_limit, max_rate) in self.max_current_limits.items():
            if current_in_amps <= upper_limit:
                actual_rate = min(
                    rate_amps_per_sec, max_rate
                )  # Ensure rate doesn't exceed maximum
                self.write(f"RATE {range_index} {actual_rate}")
                return

        raise ValueError("Current field is outside of defined rate ranges")

    def _initialize_max_current_limits(self) -> None:
        """
        Initialize the instrument with the provided current limits and rates.
        """
        for range_index, (upper_limit, max_rate) in self.max_current_limits.items():
            self.write(f"RANGE {range_index} {upper_limit}")
            self.write(f"RATE {range_index} {max_rate}")

    def write_raw(self, cmd: str) -> None:

        try:
            super().write_raw(cmd)
        except VisaIOError as err:
            # The ami communication has found to be unstable
            # so we retry the communication here
            msg = f"Got VisaIOError while writing {cmd} to instrument."
            if self._RETRY_WRITE_ASK:
                msg += f" Will retry in {self._RETRY_TIME} sec."
            self.log.exception(msg)
            if self._RETRY_WRITE_ASK:
                time.sleep(self._RETRY_TIME)
                self.device_clear()
                super().write_raw(cmd)
            else:
                raise err

    def ask_raw(self, cmd: str) -> str:

        try:
            result = super().ask_raw(cmd)
        except VisaIOError as err:
            # The communication has found to be unstable
            # so we retry the communication here
            msg = f"Got VisaIOError while asking the instrument: {cmd}"
            if self._RETRY_WRITE_ASK:
                msg += f" Will retry in {self._RETRY_TIME} sec."
            self.log.exception(msg)
            if self._RETRY_WRITE_ASK:
                time.sleep(self._RETRY_TIME)
                self.device_clear()
                result = super().ask_raw(cmd)
            else:
                raise err
        return result
