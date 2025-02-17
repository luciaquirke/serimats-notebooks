# %%
import torch
from tqdm.auto import tqdm
from transformer_lens import HookedTransformer
from jaxtyping import Float, Int, Bool
from torch import Tensor
from tqdm.auto import tqdm
import plotly.io as pio
import ipywidgets as widgets
from IPython.display import display, clear_output
import pandas as pd
import numpy as np
import plotly.express as px 
from collections import defaultdict
import matplotlib.pyplot as plt
import re
from IPython.display import display, HTML
from datasets import load_dataset
from collections import Counter
import pickle
import os
from typing import Literal

import sklearn
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score

pio.renderers.default = "notebook_connected+notebook"
device = "cuda" if torch.cuda.is_available() else "cpu"
torch.autograd.set_grad_enabled(False)
torch.set_grad_enabled(False)

from haystack_utils import get_mlp_activations
from hook_utils import save_activation
import haystack_utils
import hook_utils
import plotting_utils

%reload_ext autoreload
%autoreload 2

# %%
model = HookedTransformer.from_pretrained("EleutherAI/pythia-160m",
    center_unembed=True,
    center_writing_weights=True,
    fold_ln=True,
    device=device)

german_data = haystack_utils.load_json_data("data/german_europarl.json")[:200]

LAYER, NEURON = 8, 2994

# %%
# Snapping hooks for all position and extreme peaks
def snap_to_peak_1(value, hook):
    value[:, :, NEURON] = 2.5
    return value

def snap_to_peak_2(value, hook):
    value[:, :, NEURON] = 6.5
    return value

snap_to_peak_1_hook = [(f'blocks.{LAYER}.mlp.hook_post', snap_to_peak_1)]
snap_to_peak_2_hook = [(f'blocks.{LAYER}.mlp.hook_post', snap_to_peak_2)]

# %%
# Collect peak information and neuron activations
def get_neuron_activation_df(prompts, model, layers=(11,), start_index=5, ablation_hooks=None):
    result_dfs = []
    for prompt in tqdm(prompts):
        # Run without ablation to get original context peaks
        _, cache = model.run_with_cache(prompt)
        context_neuron_acts = cache[f'blocks.{LAYER}.mlp.hook_post'][0, start_index:, NEURON]
        context_neuron_peak_1 = (context_neuron_acts > 0.8) & (context_neuron_acts < 4.1)
        context_neuron_peak_2 = (context_neuron_acts > 4.8) & (context_neuron_acts < 10)
        prompt_df = pd.DataFrame({
            "Peak_1": context_neuron_peak_1.tolist(),
            "Peak_2": context_neuron_peak_2.tolist(),
        })
        # Run ablated to get neuron activations
        if ablation_hooks is not None:
            with model.hooks(ablation_hooks):
                _, cache = model.run_with_cache(prompt)
        neuron_dfs = []
        for layer in layers:
            for neuron in range(model.cfg.d_mlp):
                neuron_act = cache[f'blocks.{layer}.mlp.hook_post'][0, start_index:, neuron]
                neuron_dfs.append(pd.DataFrame({f"L{layer}N{neuron}": neuron_act.tolist()}))
        prompt_df = pd.concat([prompt_df] + neuron_dfs, axis=1)
        result_dfs.append(prompt_df)
    return pd.concat(result_dfs)

df = get_neuron_activation_df(german_data[:100], model, layers=(9, 10, 11,), ablation_hooks=None)
df_snap_peak_1 = get_neuron_activation_df(german_data[:100], model, layers=(9, 10, 11,), ablation_hooks=snap_to_peak_1_hook)
df_snap_peak_2 = get_neuron_activation_df(german_data[:100], model, layers=(9, 10,11,), ablation_hooks=snap_to_peak_2_hook)

# %%
layers=(9, 10, 11,)
# Compute neuron wise peak metrics
def activation_to_neuron_df(df, column_name="", mode: Literal["all", "peaks"] = "all"):
    neuron_data = []
    for layer in layers:
        for neuron in range(model.cfg.d_mlp):
            name = f"L{layer}N{neuron}"
            firing_rate = (df[name] > 0).sum() / ((df[name] > float("-inf")).sum() + 1e-10)
            peak_1_act = df.loc[df["Peak_1"], name].mean()
            peak_2_act = df.loc[df["Peak_2"], name].mean()
            # Peak 1 F1
            TP = ((df[name] > 0) & df["Peak_1"]).sum()
            if mode=="all":
                FP = ((df[name] > 0) & ~df["Peak_1"]).sum()
            else:
                FP = ((df[name] > 0) & df["Peak_2"]).sum()
            FN = ((df[name] <= 0) & df["Peak_1"]).sum()
            #TN = ((df[name] <= 0) & ~df["Peak_1"]).sum()
            peak_1_precision = TP / (TP + FP + 1e-10)
            peak_1_recall = TP / (TP + FN + 1e-10)
            peak_1_f1 = 2 * TP / (2 * TP + FP + FN + 1e-10)
            # Peak 2 F1
            TP = ((df[name] > 0) & df["Peak_2"]).sum()
            if mode=="all":
                FP = ((df[name] > 0) & ~df["Peak_2"]).sum()
            else:
                FP = ((df[name] > 0) & df["Peak_1"]).sum()
            FN = ((df[name] <= 0) & df["Peak_2"]).sum()
            #TN = ((df[name] <= 0) & ~df["Peak_2"]).sum()
            peak_2_precision = TP / (TP + FP + 1e-10)
            peak_2_recall = TP / (TP + FN + 1e-10)
            peak_2_f1 = 2 * TP / (2 * TP + FP + FN + 1e-10)
            neuron_data.append([name, firing_rate, peak_1_act, peak_2_act, peak_1_precision, peak_1_recall, peak_1_f1, peak_2_precision, peak_2_recall, peak_2_f1])
    name_append = f" ({column_name})" if column_name else ""
    return pd.DataFrame(neuron_data, columns=["Neuron", "Firing Rate" + name_append, "P1 activation"+ name_append, "P2 activation"+name_append, "P1 Precision"+ name_append, "P1 Recall"+ name_append, "P1 F1"+ name_append, "P2 Precision"+ name_append, "P2 Recall"+ name_append, "P2 F1"+ name_append])
# %%
neuron_df = activation_to_neuron_df(df, mode="peaks")
neuron_df_peak_1 = activation_to_neuron_df(df_snap_peak_1, column_name="P1 snapped", mode="peaks")
neuron_df_peak_2 = activation_to_neuron_df(df_snap_peak_2, column_name="P2 snapped", mode="peaks")
# %%
full_df = neuron_df.merge(neuron_df_peak_1, on="Neuron").merge(neuron_df_peak_2, on="Neuron")
p_2_f1_cols = ["Neuron", "Firing Rate", "P1 activation"] + [col for col in full_df.columns if ("P2 activation" in col)] + [col for col in full_df.columns if ("P2 F1" in col)]
p_1_f1_cols = ["Neuron", "Firing Rate", "P2 activation"] + [col for col in full_df.columns if ("P1 activation" in col)] + [col for col in full_df.columns if ("P1 F1" in col)]
full_df["P1 F1 increase"] = full_df["P1 F1"] - full_df["P1 F1 (P2 snapped)"]
full_df["P2 F1 increase"] = full_df["P2 F1"] - full_df["P2 F1 (P1 snapped)"]
# %%
full_df = full_df.sort_values("P1 F1", ascending=False)
full_df[p_1_f1_cols].head(10)
# %%
full_df = full_df.sort_values("P2 F1", ascending=False)
full_df[p_2_f1_cols].head(10)
# %%
px.scatter(full_df, x="P1 F1", y="P2 F1", hover_name="Neuron", title="P1 vs P2 F1")
# %%
px.scatter(full_df, x="P1 activation", y="P2 activation", hover_name="Neuron", title="P1 vs P2 activation")
# %%
px.scatter(full_df, x="P1 F1", y="P1 F1 (P2 snapped)", hover_name="Neuron", title="L11 Neurons' P1 F1 vs P1 F1 (P2 snapped)")
# %%
px.scatter(full_df, x="P2 F1", y="P2 F1 (P1 snapped)", hover_name="Neuron", title="L11 Neurons' P2 F1 vs P2 F1 (P1 snapped)")
# %%
len(df)
# %%
full_df = full_df.sort_values("P1 F1 increase", ascending=False)
full_df.loc[full_df["P1 F1"]>0.7,p_1_f1_cols].head(10)
# %%
full_df = full_df.sort_values("P2 F1 increase", ascending=False)
full_df.loc[full_df["P2 F1"]>0.7, p_2_f1_cols].head(10)
# %%
