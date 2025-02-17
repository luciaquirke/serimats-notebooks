import random
import argparse
import pickle
import os
import gzip
from pathlib import Path

import pandas as pd
import numpy as np
import torch
from einops import einops
from transformer_lens import HookedTransformer
from tqdm.auto import tqdm
import plotly.io as pio
import ipywidgets as widgets
from IPython.display import display, HTML
from sklearn import preprocessing
from sklearn.model_selection import train_test_split
import plotly.express as px
import plotly.graph_objects as go

from neel_plotly import *
import haystack_utils
import probing_utils

# %reload_ext autoreload
# %autoreload 2
SEED = 42

def set_seeds():
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    random.seed(SEED)

    pio.renderers.default = "notebook_connected+notebook"


def get_model(model_name: str, checkpoint: int) -> HookedTransformer:
    model = HookedTransformer.from_pretrained(
        model_name,
        checkpoint_index=checkpoint,
        center_unembed=True,
        center_writing_weights=True,
        fold_ln=True,
        device="cuda" if torch.cuda.is_available() else "cpu",
    )
    return model


def preload_models(model_name: str) -> int:
    """Preload models into cache so we can iterate over them quickly and return the model checkpoint count."""
    i = 0
    try:
        with tqdm(total=None) as pbar:
            while True:
                get_model(model_name, i)
                i += 1
                pbar.update(1)

    except IndexError:
        return i


def load_language_data() -> dict:
    """
    Returns: dictionary keyed by language code, containing 200 lines of each language included in the Europarl dataset.
    """
    lang_data = {}
    lang_data["en"] = haystack_utils.load_json_data("data/english_europarl.json")[:200]

    europarl_data_dir = Path("data/europarl/")
    for file in os.listdir(europarl_data_dir):
        if file.endswith(".txt"):
            lang = file.split("_")[0]
            lang_data[lang] = haystack_utils.load_txt_data(europarl_data_dir.joinpath(file))

    for lang in lang_data.keys():
        print(lang, len(lang_data[lang]))
    return lang_data


def train_probe(
    positive_data: torch.Tensor, negative_data: torch.Tensor
) -> tuple[float, float]:
    labels = np.concatenate([np.ones(len(positive_data)), np.zeros(len(negative_data))])
    data = np.concatenate([positive_data.cpu().numpy(), negative_data.cpu().numpy()])
    scaler = preprocessing.StandardScaler().fit(data)
    data = scaler.transform(data)
    x_train, x_test, y_train, y_test = train_test_split(
        data, labels, test_size=0.2, random_state=SEED
    )
    probe = probing_utils.get_probe(x_train, y_train, max_iter=2000)
    f1, mcc = probing_utils.get_probe_score(probe, x_test, y_test)
    return f1, mcc


def save_activation(value, hook):
    hook.ctx['activation'] = value
    return value


def get_mlp_activations(
    prompts: list[str], layer: int, model: HookedTransformer
) -> torch.Tensor:
    act_label = f"blocks.{layer}.mlp.hook_post"

    acts = []
    for prompt in prompts:
        with model.hooks([(act_label, save_activation)]):
            model(prompt)
            act = model.hook_dict[act_label].ctx["activation"][:, 10:400, :]
        act = einops.rearrange(act, "batch pos n_neurons -> (batch pos) n_neurons")
        acts.append(act)
    acts = torch.concat(acts, dim=0)
    return acts


def zero_ablate_hook(value, hook):
    value[:, :, :] = 0
    return value


def get_layer_probe_performance(
    model: HookedTransformer,
    checkpoint: int,
    layer: int,
    german_data: np.array,
    non_german_data: np.array,
) -> pd.DataFrame:
    """Probe performance for each neuron."""

    german_activations = get_mlp_activations(german_data[:30], layer, model)[:10_000]
    non_german_activations = get_mlp_activations(non_german_data[:30], layer, model)[
        :10_000
    ]

    mean_german_activations = german_activations.mean(0).cpu().numpy()
    mean_non_german_activations = non_german_activations.mean(0).cpu().numpy()

    f1s = []
    mccs = []
    for neuron in range(model.cfg.d_mlp):
        f1, mcc = train_probe(
            german_activations[:, neuron].unsqueeze(-1),
            non_german_activations[:, neuron].unsqueeze(-1),
        )
        f1s.append(f1)
        mccs.append(mcc)

    checkpoint_neuron_labels = [
        f"C{checkpoint}L{layer}N{i}" for i in range(model.cfg.d_mlp)
    ]
    neuron_labels = [f"L{layer}N{i}" for i in range(model.cfg.d_mlp)]
    neuron_indices = [i for i in range(model.cfg.d_mlp)]

    layer_df = pd.DataFrame(
        {
            "Label": checkpoint_neuron_labels,
            "NeuronLabel": neuron_labels,
            "Neuron": neuron_indices,
            "F1": f1s,
            "MCC": mccs,
            "MeanGermanActivation": mean_german_activations,
            "MeanNonGermanActivation": mean_non_german_activations,
            "Checkpoint": [checkpoint] * len(checkpoint_neuron_labels),
            "Layer": [layer] * len(checkpoint_neuron_labels),
        }
    )
    return layer_df


def get_layer_ablation_loss(
    model: HookedTransformer, german_data: list, checkpoint: int, layer: int
):
    loss_data = []

    for prompt in german_data[:100]:
        loss = model(prompt, return_type="loss").item()
        with model.hooks([(f"blocks.{layer}.mlp.hook_post", zero_ablate_hook)]):
            ablated_loss = model(prompt, return_type="loss").item()
        loss_difference = ablated_loss - loss
        loss_data.append([checkpoint, layer, loss_difference, loss, ablated_loss])

    layer_df = pd.DataFrame(
        loss_data,
        columns=[
            "Checkpoint",
            "Layer",
            "LossDifference",
            "OriginalLoss",
            "AblatedLoss",
        ],
    )
    return layer_df


def get_language_losses(
    model: HookedTransformer, checkpoint: int, lang_data: dict
) -> pd.DataFrame:
    data = []
    for lang in lang_data.keys():
        losses = []
        for prompt in lang_data[lang]:
            loss = model(prompt, return_type="loss").item()
            losses.append(loss)
        data.append([checkpoint, lang, np.mean(losses)])

    return pd.DataFrame(data, columns=["Checkpoint", "Language", "Loss"])


def run_probe_analysis(
    model_name: str,
    num_checkpoints: int,
    lang_data: dict,
    output_dir: Path,
) -> None:
    """Collect several dataframes covering whole layer ablation losses, ngram loss, language losses, and neuron probe performance."""
    model = get_model(model_name, 0)
    n_layers = model.cfg.n_layers

    german_data = lang_data["de"]
    non_german_data = np.concatenate([lang_data[lang] for lang in lang_data.keys() if lang != "de"])
    np.random.shuffle(non_german_data)
    non_german_data = non_german_data[:200].tolist()

    probe_dfs = []
    layer_ablation_dfs = []
    lang_loss_dfs = []
    with tqdm(total=num_checkpoints * n_layers) as pbar:
        for checkpoint in range(num_checkpoints):
            model = get_model(model_name, checkpoint)
            for layer in range(n_layers):
                partial_probe_df = get_layer_probe_performance(
                    model, checkpoint, layer, german_data, non_german_data
                )
                probe_dfs.append(partial_probe_df)

                partial_layer_ablation_df = get_layer_ablation_loss(
                    model, german_data, checkpoint, layer
                )

                layer_ablation_dfs.append(partial_layer_ablation_df)
                lang_loss_dfs.append(get_language_losses(model, checkpoint, lang_data))

                # Save progress to allow for checkpointing the analysis
                with open(
                    output_dir.joinpath(model_name + "_checkpoint_features.pkl.gz"), "wb"
                ) as f:
                    pickle.dump(
                        {
                            "probe": probe_dfs,
                            "layer_ablation": layer_ablation_dfs,
                            "lang_loss": lang_loss_dfs,
                        },
                        f,
                    )

                pbar.update(1)

    # Open the pickle file
    with open(output_dir.joinpath(model_name + "_checkpoint_features.pkl.gz"), "rb") as f:
        data = pickle.load(f)

    # Concatenate the dataframes
    data = {dfs_name: pd.concat(dfs) for dfs_name, dfs in data.items()}

    # Compress with gzip using high compression and save
    with gzip.open(
        output_dir.joinpath(model_name + "_checkpoint_features.pkl.gz"), "wb", compresslevel=9
    ) as f_out:
        pickle.dump(data, f_out)


def analyze_model_checkpoints(model_name: str, output_dir: Path) -> None:
    set_seeds()

    # Will take about 50GB of disk space for Pythia 70M models
    num_checkpoints = preload_models(model_name)

    # Load probe data
    lang_data = load_language_data()

    run_probe_analysis(
        model_name, num_checkpoints, lang_data, Path(output_dir)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--model",
        default="EleutherAI/pythia-70m",
        help="Name of model from TransformerLens",
    )
    parser.add_argument("--output_dir", default="feature_formation")

    args = parser.parse_args()

    save_path = os.path.join(args.output_dir, args.model)
    os.makedirs(save_path, exist_ok=True)

    analyze_model_checkpoints(args.model, args.output_dir)

    save_image_path = os.path.join(save_path, "images")
    os.makedirs(save_image_path, exist_ok=True)
    
    # process_data(args.model, Path(save_path), Path(save_image_path))


