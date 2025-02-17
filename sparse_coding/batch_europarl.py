import sys

sys.path.append("../")  # Add the parent directory to the system path
import torch
from transformer_lens import HookedTransformer
import plotly.io as pio


pio.renderers.default = "notebook_connected"
device = (
    "cuda"
    if torch.cuda.is_available()
    else "mps"
    if torch.backends.mps.is_available()
    else "cpu"
)
torch.autograd.set_grad_enabled(False)
torch.set_grad_enabled(False)

import utils.haystack_utils as haystack_utils
import utils.autoencoder_utils as autils

if __name__ == "__main__":
    model = HookedTransformer.from_pretrained(
        "EleutherAI/pythia-70m",
        center_unembed=True,
        center_writing_weights=True,
        fold_ln=True,
        device=device,
    )

    dataset = "europarl"
    language = "de"
    input_path = f"sparse_coding/data/{dataset}/{language}_samples.json"
    output_path = f"sparse_coding/data/{dataset}/{language}_batched.pt"

    german_data = haystack_utils.load_json_data(input_path)

    seq_len = 127
    german_tensor = autils.batch_prompts(german_data, model, seq_len)

    torch.save(german_tensor, output_path)
    del german_tensor, german_data

    # english_data = haystack_utils.load_json_data("sparse_coding/data/europarl/en_samples.json")
    # english_tensor = batch_prompts(english_data, model, seq_len)
    # torch.save(english_tensor, "sparse_coding/data/europarl/en_batched.pt")
