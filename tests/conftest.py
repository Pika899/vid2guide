import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Isolate every test run: data in a temp folder, keys in memory, fixed token.
_DATA = Path(tempfile.mkdtemp(prefix="vid2guide-test-"))
os.environ["VID2GUIDE_DATA"] = str(_DATA)
os.environ["VID2GUIDE_TOKEN"] = "test-token"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


keyring.set_keyring(MemoryKeyring())

from app import paths  # noqa: E402

DATA = _DATA
HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture(autouse=True)
def clean_data():
    for d in ("input_videos", "transcriptions", "output_summary", "temp_chunks"):
        shutil.rmtree(DATA / d, ignore_errors=True)
    paths.ensure_dirs()
    yield


SAMPLE_TRANSCRIPT = """# Trascrizione: lezione.mp4
# Modello: whisper-small | Lingua: it | Durata: 1m 00s
# Generata il 2026-10-09 10:00 da main_pipeline.py
#

[00:00:00] Benvenuti, oggi parliamo di prompt.
[00:00:05] Primo concetto: il ruolo.
[00:00:12] Adesso vi faccio vedere il prompt che uso.
[00:00:20] Secondo concetto: il formato della risposta.
[00:00:41] Ricapitoliamo i tre passi.
"""
