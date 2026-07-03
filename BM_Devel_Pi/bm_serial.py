import os
import serial

try:
	import yaml
except Exception:  # pragma: no cover - runtime fallback if PyYAML is unavailable
	yaml = None


# Local runtime config. This path is intentionally the deployed runtime path,
# not the git checkout path. Override with BM_CAMERA_CONFIG_PATH if needed.
BM_CAMERA_CONFIG_PATH = os.environ.get(
	"BM_CAMERA_CONFIG_PATH",
	"/home/pi/BM_Devel_Pi/camera_schedule.yaml",
)

# Spotter transmit-data network selector values.
# These bytes are appended after topic "spotter/transmit-data".
#
# 0x01 = legacy sat/cell fallback queue, observed as MS_Q_LEGACY.
# 0x02 = cellular-only queue, observed as MS_Q_CELLULAR_ONLY.
#
# Do not confuse this byte with get_pub_header()'s BM serial publish header
# bytearray.fromhex("0101").
SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE = 0x01
SPOTTER_NETWORK_CELLULAR_ONLY_VALUE = 0x02
SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK = bytes([SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE])
SPOTTER_NETWORK_CELLULAR_ONLY = bytes([SPOTTER_NETWORK_CELLULAR_ONLY_VALUE])

# Safe fallback if camera_schedule.yaml is missing bm_serial.network_type.
# Production large-message deployments should explicitly set this in YAML.
DEFAULT_SPOTTER_TRANSMIT_NETWORK_TYPE = SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE


def _load_camera_schedule(config_path=None):
	path = config_path or BM_CAMERA_CONFIG_PATH
	if yaml is None:
		return {}
	try:
		if not os.path.exists(path):
			return {}
		with open(path, "r", encoding="utf-8") as f:
			data = yaml.safe_load(f) or {}
		return data if isinstance(data, dict) else {}
	except Exception:
		return {}


def load_bm_serial_config(config_path=None):
	"""Return the bm_serial block from camera_schedule.yaml, if present."""
	data = _load_camera_schedule(config_path=config_path)
	cfg = data.get("bm_serial", {})
	return cfg if isinstance(cfg, dict) else {}


def parse_network_type(value=None):
	"""Parse a network selector from YAML-friendly forms.

	Accepted values:
	  1, "1", "0x01", "legacy", "cellular_iri_fallback"
	  2, "2", "0x02", "cellular_only", "cell-only"
	"""
	if value is None:
		value = DEFAULT_SPOTTER_TRANSMIT_NETWORK_TYPE

	if isinstance(value, bytes):
		if value in (SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK, SPOTTER_NETWORK_CELLULAR_ONLY):
			return value
		raise ValueError(f"Invalid network_type bytes: {value!r}")

	if isinstance(value, str):
		text = value.strip().lower().replace("-", "_")
		aliases = {
			"legacy": SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE,
			"sat_cell": SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE,
			"sat_cell_fallback": SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE,
			"cellular_iri_fallback": SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE,
			"cellular_iridium_fallback": SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE,
			"cellular_only": SPOTTER_NETWORK_CELLULAR_ONLY_VALUE,
			"cell_only": SPOTTER_NETWORK_CELLULAR_ONLY_VALUE,
		}
		if text in aliases:
			value = aliases[text]
		else:
			value = int(text, 0)

	value = int(value)
	if value == SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE:
		return SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK
	if value == SPOTTER_NETWORK_CELLULAR_ONLY_VALUE:
		return SPOTTER_NETWORK_CELLULAR_ONLY

	raise ValueError("network_type must be 0x01/1 or 0x02/2")


def network_type_value(network_type_bytes):
	return int.from_bytes(parse_network_type(network_type_bytes), "little")


def describe_network_type(network_type_bytes):
	value = network_type_value(network_type_bytes)
	if value == SPOTTER_NETWORK_CELLULAR_IRI_FALLBACK_VALUE:
		return "0x01 legacy sat/cell fallback"
	if value == SPOTTER_NETWORK_CELLULAR_ONLY_VALUE:
		return "0x02 cellular-only"
	return f"0x{value:02x} unknown"


def load_network_type_from_config(config_path=None):
	cfg = load_bm_serial_config(config_path=config_path)
	return parse_network_type(cfg.get("network_type", DEFAULT_SPOTTER_TRANSMIT_NETWORK_TYPE))


class BristlemouthSerial:
	def __init__(self, uart=None, node_id=0xC0FFEEEEF0CACC1A, network_type=None):
		self.node_id = node_id
		if uart is None:
			self.uart = serial.Serial('/dev/ttyAMA0', 115200)  # Adjust the serial port, ttyAMA0 as needed, ttyS0
		else:
			self.uart = uart
		if network_type is None:
			self.network_type = load_network_type_from_config()
		else:
			self.network_type = parse_network_type(network_type)

	def set_network_type(self, network_type=None):
		if network_type is None:
			self.network_type = load_network_type_from_config()
		else:
			self.network_type = parse_network_type(network_type)
		return self.network_type

	def get_network_type_value(self):
		return network_type_value(self.network_type)

	def describe_network_type(self):
		return describe_network_type(self.network_type)

	def spotter_tx(self, data, network_type=None):
		topic = b"spotter/transmit-data"

		if isinstance(data, str):
			data = data.encode("utf-8")
		elif isinstance(data, bytearray):
			data = bytes(data)

		if not isinstance(data, bytes):
			raise TypeError(f"spotter_tx data must be bytes, bytearray, or str; got {type(data)}")

		tx_network_type = parse_network_type(network_type) if network_type is not None else self.network_type

		packet = (
			self.get_pub_header()
			+ len(topic).to_bytes(2, "little")
			+ topic
			# Network selector byte for spotter/transmit-data. Do not confuse
			# this with get_pub_header()'s BM serial publish header.
			+ tx_network_type
			+ data
		)
		cobs = self.finalize_packet(packet)
		return self.uart.write(cobs)

	def spotter_log(self, filename, data):
		topic = b"spotter/fprintf"
		packet = (
			self.get_pub_header()
			+ len(topic).to_bytes(2, "little")
			+ topic
			+ b"\x00" * 8
			+ len(filename).to_bytes(2, "little")
			+ (len(data) + 1).to_bytes(2, "little")
			+ filename.encode()
			+ data.encode()
			+ b"\n"
		)
		cobs = self.finalize_packet(packet)
		return self.uart.write(cobs)

	def finalize_packet(self, packet):
		checksum = self.crc(0, packet)
		packet[2] = checksum & 0xFF
		packet[3] = (checksum >> 8) & 0xFF
		cobs = self.cobs_encode(packet) + b"\x00"
		return cobs

	def get_pub_header(self):
		return (
			bytearray.fromhex("02000000")
			+ self.node_id.to_bytes(8, "little")
			+ bytearray.fromhex("0101")
		)

	def cobs_encode(self, in_bytes):
		final_zero = True
		out_bytes = bytearray()
		idx = 0
		search_start_idx = 0
		for in_char in in_bytes:
			if in_char == 0:
				final_zero = True
				out_bytes.append(idx - search_start_idx + 1)
				out_bytes += in_bytes[search_start_idx:idx]
				search_start_idx = idx + 1
			else:
				if idx - search_start_idx == 0xFD:
					final_zero = False
					out_bytes.append(0xFF)
					out_bytes += in_bytes[search_start_idx : idx + 1]
					search_start_idx = idx + 1
			idx += 1
		if idx != search_start_idx or final_zero:
			out_bytes.append(idx - search_start_idx + 1)
			out_bytes += in_bytes[search_start_idx:idx]
		return bytes(out_bytes)

	def crc(self, seed, src):
		e, f = 0, 0
		for i in src:
			e = (seed ^ i) & 0xFF
			f = e ^ ((e << 4) & 0xFF)
			seed = (seed >> 8) ^ (((f << 8) & 0xFFFF) ^ ((f << 3) & 0xFFFF)) ^ (f >> 4)
		return seed

	def deinit(self):
		if self.uart:
			self.uart.close()
			self.uart = None
