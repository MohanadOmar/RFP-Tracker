"""All sources expose a fetch() -> list[dict] function."""
from . import txsmartbuy, samgov, bidnet, civcast

ALL_SOURCES = [txsmartbuy, samgov, bidnet, civcast]
