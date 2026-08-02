"""Provider interfaces plus their offline (default) implementations.

Live adapters live in sibling modules and are never imported from here: importing
this package must not pull in torch, speechbrain or the network stack.
"""

from voicekin.adapters.deliver import DeliveryPayload, DeliveryReceipt, DeviceDeliverer
from voicekin.adapters.deliver_filesink import FileSinkDeliverer
from voicekin.adapters.embedder import Embedding, SpeakerEmbedder
from voicekin.adapters.embedder_spectral import SpectralStatsEmbedder
from voicekin.adapters.synth import Synthesizer
from voicekin.adapters.synth_stub import FormantStubSynthesizer

__all__ = [
    "DeliveryPayload",
    "DeliveryReceipt",
    "DeviceDeliverer",
    "Embedding",
    "FileSinkDeliverer",
    "FormantStubSynthesizer",
    "SpeakerEmbedder",
    "SpectralStatsEmbedder",
    "Synthesizer",
]
