"""ShareG constants shared by the networking core and the UI layer."""

APP_NAME = "ShareG"

# ---- UDP multicast discovery -------------------------------------------------
MULTICAST_GROUP = "239.255.83.72"   # ShareG discovery group (site-local)
MULTICAST_PORT = 50711
DISCOVERY_INTERVAL = 2.0            # seconds between presence announcements
DISCOVERY_TTL = 4                   # multicast hop limit (stays on local nets)
PEER_TIMEOUT = 7.0                  # seconds without announce before stale

# ---- TCP transfer -------------------------------------------------------------
TRANSFER_PORT = 50712               # listening port for incoming transfers
DEFAULT_CHUNK_SIZE = 256 * 1024     # 256 KiB chunks for file transfer

# ---- Protocol magic -----------------------------------------------------------
PROTOCOL_MAGIC = b"SHRG"            # first bytes of every TCP message
PROTOCOL_VERSION = 1

# ---- Heartbeat / health -------------------------------------------------------
HEARTBEAT_INTERVAL = 5.0            # seconds between ping rounds
HEARTBEAT_TIMEOUT = 4.0             # seconds to wait for a pong

# ---- Pairing negotiation ------------------------------------------------------
# The pairing handshake may stay open while a human answers the
# "Do you want to connect to this device?" dialog on the receiving device.
# The sender must therefore wait longer than the receiver's dialog timeout
# (PAIR_PROMPT_TIMEOUT) or the first pairing attempt would time out before
# the user can answer.
PAIR_PROMPT_TIMEOUT = 120.0         # receiver: how long the dialog may stay open
PAIR_NEGOTIATION_TIMEOUT = 150.0    # sender: how long to wait for accept/reject

# ---- Pairing ------------------------------------------------------------------
PAIRING_STORE_FILENAME = "shareg_paired_devices.json"
IDENTITY_STORE_FILENAME = "shareg_identity.json"

# ---- Message kinds (first JSON frame on a TCP transfer connection) -----------
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_TEXT = "text"
MSG_FILE_BEGIN = "file_begin"
MSG_FILE_CHUNK = "file_chunk"
MSG_FILE_END = "file_end"
MSG_FOLDER_BEGIN = "folder_begin"
MSG_FOLDER_END = "folder_end"
MSG_OK = "ok"
MSG_ERROR = "error"
MSG_PAIR_REQUEST = "pair_request"
MSG_PAIR_ACCEPT = "pair_accept"
MSG_PAIR_REJECT = "pair_reject"
