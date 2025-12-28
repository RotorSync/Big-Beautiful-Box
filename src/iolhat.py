#
# IOL-HAT Communication Library
# Based on the Pinetek Networks iol-hat distribution
# https://github.com/Pinetek-Networks/iol-hat/
#
# Modified for IOL Dashboard with:
# - Configurable TCP ports
# - Improved error handling
# - Logging support
#

import struct
import socket
import time
import logging

logger = logging.getLogger(__name__)

# Configuration
TCP_IP = '127.0.0.1'
BUFFER_SIZE = 1024

# TCP ports for IOL master (both set to 12011 for single-master setup)
TCP_PORT1 = 12011
TCP_PORT2 = 12011

# Command results
CMD_SUCCESS = 1
CMD_FAIL = 0

# Logging verbosity
verbose = False


def set_verbose(enabled: bool) -> None:
    """Enable or disable verbose logging."""
    global verbose
    verbose = enabled


def _get_tcp_port(port: int) -> tuple:
    """
    Get TCP port and adjusted port number for IOL master.

    Args:
        port: Logical port (0-3)

    Returns:
        Tuple of (tcp_port, adjusted_port)
    """
    if port < 2:
        return TCP_PORT1, port
    else:
        return TCP_PORT2, port - 2


def power(port: int, status: int) -> int:
    """
    Set power state for an IOL port.

    Args:
        port: IOL port number (0-3)
        status: Power state (0=off, 1=on)

    Returns:
        CMD_SUCCESS or raises exception
    """
    if port not in [0, 1, 2, 3]:
        raise ValueError("Port out of range (must be 0-3)")

    if status not in [0, 1]:
        raise ValueError("Status out of range (must be 0 or 1)")

    tcp_port, adj_port = _get_tcp_port(port)
    message = struct.pack("!BBB", 1, adj_port, status)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))
        s.send(message)
        data = s.recv(BUFFER_SIZE)

        if len(data) == 2:
            raw_data = struct.unpack("!BB", data)
            logger.error(f"Power error (port={port}): {raw_data[1]}")
            raise Exception(f"Power command error: {raw_data[1]}")
        else:
            if verbose:
                logger.debug(f"Power set: port={port}, status={status}")

    except Exception as e:
        logger.error(f"Power error (port={port}): {e}")
        raise

    finally:
        s.close()

    return CMD_SUCCESS


def pd(port: int, len_out: int, len_in: int, pd_out: bytes) -> bytes:
    """
    Read/write process data from IOL device.

    Args:
        port: IOL port number (0-3)
        len_out: Output data length
        len_in: Expected input data length
        pd_out: Output data bytes (or None for read-only)

    Returns:
        Input data bytes from device
    """
    if verbose:
        logger.debug(f"PD: port={port}, len_out={len_out}, len_in={len_in}")

    if port not in [0, 1, 2, 3]:
        logger.error("PD: port out of range")
        raise ValueError("Port out of range (must be 0-3)")

    tcp_port, adj_port = _get_tcp_port(port)

    # Build message
    message_buffer = bytearray(4)
    struct.pack_into("!BBBB", message_buffer, 0, 3, adj_port, len_out, len_in)

    out_buffer = message_buffer
    if len_out > 0 and pd_out:
        out_buffer += pd_out

    if verbose:
        logger.debug(f"PD: sending {out_buffer.hex()}")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))
        s.send(out_buffer)
        rcv_buffer = s.recv(BUFFER_SIZE)

        if verbose:
            logger.debug(f"PD: received {rcv_buffer.hex()}")

        return_data = rcv_buffer[4:]

    except Exception as e:
        logger.error(f"PD error (port={port}): {e}")
        raise ValueError(f"PD error: {e}")

    finally:
        s.close()

    time.sleep(4 / 1000)  # Small delay to prevent overload
    return return_data


# LED states
LED_OFF = 0
LED_GREEN = 1
LED_RED = 2


def led(port: int, status: int) -> int:
    """
    Set LED state for an IOL port.

    Args:
        port: IOL port number (0-3)
        status: LED state (0=off, 1=green, 2=red)

    Returns:
        CMD_SUCCESS or raises exception
    """
    if verbose:
        logger.debug(f"LED: port={port}, status={status}")

    if port not in [0, 1, 2, 3]:
        raise ValueError("Port out of range (must be 0-3)")

    if status not in [0, 1, 2, 3]:
        raise ValueError("LED value out of range (must be 0-3)")

    tcp_port, adj_port = _get_tcp_port(port)
    message = struct.pack("!BBB", 2, adj_port, status)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))
        s.send(message)
        data = s.recv(BUFFER_SIZE)

        if len(data) == 2:
            raw_data = struct.unpack("!BB", data)
            error_msg = get_error_message(raw_data[1])
            logger.error(f"LED error (port={port}): {error_msg}")
            raise Exception(f"LED command error: {error_msg}")
        else:
            if verbose:
                logger.debug(f"LED set: port={port}, status={status}")

    except Exception as e:
        logger.error(f"LED error (port={port}): {e}")
        raise

    finally:
        s.close()

    time.sleep(4 / 1000)
    return CMD_SUCCESS


def read(port: int, index: int, subindex: int, length: int) -> bytes:
    """
    Read ISDU data from IOL device.

    Args:
        port: IOL port number (0-3)
        index: Parameter index
        subindex: Parameter subindex
        length: Expected data length

    Returns:
        Data bytes from device
    """
    if verbose:
        logger.debug(f"READ: port={port}, index={index}, subindex={subindex}, length={length}")

    if port not in [0, 1, 2, 3]:
        raise ValueError("Port out of range (must be 0-3)")

    if not 0 <= index <= 0xFFFF:
        raise ValueError("Index out of range")

    if not 0 <= subindex <= 0xFF:
        raise ValueError("Subindex out of range")

    tcp_port, adj_port = _get_tcp_port(port)
    message = struct.pack("!BBHBB", 4, adj_port, index, subindex, length)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))
        s.send(message)
        data = s.recv(BUFFER_SIZE)

        data_len = len(data)
        if verbose:
            logger.debug(f"READ: received {data_len} bytes")

        if data_len == 2:
            raw_data = struct.unpack("!BB", data)
            error_msg = get_error_message(raw_data[1])
            logger.error(f"READ TCP error: {error_msg}")
            raise Exception(f"READ error: {error_msg}")

        elif data_len == 4:
            raw_data = struct.unpack("!BBH", data)
            logger.error(f"READ IO-Link error: {hex(raw_data[2])}")
            return b''

        else:
            return data[6:]

    except Exception as e:
        logger.error(f"READ exception: {e}")
        raise

    finally:
        s.close()


def write(port: int, index: int, subindex: int, length: int, write_data: bytes) -> int:
    """
    Write ISDU data to IOL device.

    Args:
        port: IOL port number (0-3)
        index: Parameter index
        subindex: Parameter subindex
        length: Data length
        write_data: Data bytes to write

    Returns:
        CMD_SUCCESS or CMD_FAIL
    """
    if verbose:
        logger.debug(f"WRITE: port={port}, index={index}, subindex={subindex}, length={length}")

    if port not in [0, 1, 2, 3]:
        raise ValueError("Port out of range (must be 0-3)")

    if not 0 <= index <= 0xFFFF:
        raise ValueError("Index out of range")

    if not 0 <= subindex <= 0xFF:
        raise ValueError("Subindex out of range")

    tcp_port, adj_port = _get_tcp_port(port)
    snd_message = struct.pack(
        "!BBHBB%ds" % len(write_data),
        5, adj_port, index, subindex, length, write_data
    )

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))

        if verbose:
            logger.debug(f"WRITE: sending {len(snd_message)} bytes")

        s.send(snd_message)
        rcv_message = s.recv(BUFFER_SIZE)
        rcv_len = len(rcv_message)

        if rcv_len == 2:
            raw_data = struct.unpack("!BB", rcv_message)
            error_msg = get_error_message(raw_data[1])
            logger.error(f"WRITE TCP error: {error_msg}")
            raise Exception(f"WRITE error: {error_msg}")

        elif rcv_len == 4:
            raw_data = struct.unpack("!BBH", rcv_message)
            logger.error(f"WRITE IO-Link error: {hex(raw_data[2])}")
            return CMD_FAIL

    except Exception as e:
        logger.error(f"WRITE exception: {e}")
        raise

    finally:
        s.close()

    time.sleep(4 / 1000)
    return CMD_SUCCESS


class IolStatus:
    """IOL port status information."""

    def __init__(
        self,
        pd_in_valid: int = 0,
        pd_out_valid: int = 0,
        transmission_rate: int = 0,
        master_cycle_time: int = 0,
        pd_in_length: int = 0,
        pd_out_length: int = 0,
        vendor_id: int = 0,
        device_id: int = 0,
        power: int = 0
    ):
        self.pd_in_valid = pd_in_valid
        self.pd_out_valid = pd_out_valid
        self.transmission_rate = transmission_rate
        self.master_cycle_time = master_cycle_time
        self.pd_in_length = pd_in_length
        self.pd_out_length = pd_out_length
        self.vendor_id = vendor_id
        self.device_id = device_id
        self.power = power

    @classmethod
    def from_buffer(cls, buffer: bytes) -> 'IolStatus':
        """Parse status from buffer."""
        if len(buffer) < 13:
            raise ValueError("Buffer too short for IolStatus")

        return cls(
            pd_in_valid=buffer[0],
            pd_out_valid=buffer[1],
            transmission_rate=buffer[2],
            master_cycle_time=buffer[3],
            pd_in_length=buffer[4],
            pd_out_length=buffer[5],
            vendor_id=int.from_bytes(buffer[6:8], byteorder='little'),
            device_id=int.from_bytes(buffer[8:12], byteorder='little'),
            power=buffer[12]
        )

    def __repr__(self) -> str:
        return (
            f"IolStatus(pd_in_valid={self.pd_in_valid}, "
            f"vendor_id={self.vendor_id}, device_id={self.device_id})"
        )


def read_status(port: int) -> IolStatus:
    """
    Read status from IOL port.

    Args:
        port: IOL port number (0-3)

    Returns:
        IolStatus object
    """
    if verbose:
        logger.debug(f"STATUS: port={port}")

    if port not in [0, 1, 2, 3]:
        raise ValueError("Port out of range (must be 0-3)")

    tcp_port, adj_port = _get_tcp_port(port)
    message = struct.pack("!BB", 6, adj_port)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TCP_IP, tcp_port))
        s.send(message)
        data = s.recv(BUFFER_SIZE)

        data_len = len(data)
        if verbose:
            logger.debug(f"STATUS: received {data_len} bytes")

        if data_len == 2:
            raw_data = struct.unpack("!BB", data)
            error_msg = get_error_message(raw_data[1])
            logger.error(f"STATUS error: {error_msg}")
            raise Exception(f"STATUS error: {error_msg}")

        elif data_len != 15:
            logger.error(f"STATUS wrong length: expected 15, got {data_len}")
            raise Exception("STATUS wrong response length")

        else:
            return IolStatus.from_buffer(data[2:])

    except Exception as e:
        logger.error(f"STATUS exception: {e}")
        raise

    finally:
        s.close()


def get_error_message(error_code: int) -> str:
    """Get human-readable error message for error code."""
    error_messages = {
        0xFF: "General error",
        0x01: "Invalid length",
        0x02: "Function not supported",
        0x03: "Power failure",
        0x04: "Invalid port ID",
        0x05: "Internal error"
    }
    return error_messages.get(error_code, f"Unknown error ({error_code})")


# Backward compatibility aliases
readStatus = read_status
