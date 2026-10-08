import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="session")
def tiny_model_dir(tmp_path_factory):
    """A tiny random Llama with Nemotron 3 Nano's chat template (see tiny_model.py). Needs the training extras."""
    for mod in ("torch", "transformers", "tokenizers"):
        if importlib.util.find_spec(mod) is None:
            pytest.skip(f"{mod} isn't installed (pip install -e '.[train]')")
    import tiny_model
    return tiny_model.build(tmp_path_factory.mktemp("tiny-model"))
