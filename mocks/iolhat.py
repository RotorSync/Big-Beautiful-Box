# Mock iolhat for testing
import struct

def power(port, status):
    pass

def pd(port, offset, length, data):
    totalizer = 100.0
    flow_rate = 0.5
    result = b'\x00' * 4 + struct.pack('>f', totalizer) + struct.pack('>f', flow_rate) + b'\x00' * 3
    return result
