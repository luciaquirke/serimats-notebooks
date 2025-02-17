
#%% 

import pickle
from typing import Literal
import torch
from tqdm.auto import tqdm
from transformer_lens import HookedTransformer, ActivationCache, utils
from jaxtyping import Float, Int, Bool
from torch import Tensor
from tqdm.auto import tqdm
import plotly.io as pio
import ipywidgets as widgets
from IPython.display import display, clear_output
import plotly.graph_objects as go
import numpy as np
import pandas as pd
import json
import plotting_utils
import hook_utils
import plotly.express as px


pio.renderers.default = "notebook_connected"
device = "cuda" if torch.cuda.is_available() else "cpu"
torch.autograd.set_grad_enabled(False)
torch.set_grad_enabled(False)

import haystack_utils

%reload_ext autoreload
%autoreload 2
#%%

model = HookedTransformer.from_pretrained("EleutherAI/pythia-70m",
    center_unembed=True,
    center_writing_weights=True,
    fold_ln=True,
    device=device)

activate_neurons_fwd_hooks, deactivate_neurons_fwd_hooks = haystack_utils.get_context_ablation_hooks(3, [669], model)
all_ignore, _ = haystack_utils.get_weird_tokens(model, plot_norms=False)

german_data = haystack_utils.load_json_data("data/german_europarl.json")[:200]
common_tokens = haystack_utils.get_common_tokens(german_data, model, all_ignore, k=100)

# Sort tokens into new word vs continuation
new_word_tokens = []
continuation_tokens = []
for token in common_tokens:
    str_token = model.to_single_str_token(token.item())
    if str_token.startswith(" "):
        new_word_tokens.append(token)
    else:
        continuation_tokens.append(token)
new_word_tokens = torch.stack(new_word_tokens)
continuation_tokens = torch.stack(continuation_tokens)

# %%
# all_prompts = {}
# for option in tqdm(options):
#     # Create global prompts
#     option_prompts = haystack_utils.generate_random_prompts(option, model, common_tokens, 5000, length=20).cpu()
#     all_prompts[option] = option_prompts

# Only overwrite if really needed!
# with open("data/prompts.pkl", "wb") as f:
#     pickle.dump(all_prompts, f)

with open("data/prompts.pkl", "rb") as f:
    all_prompts = pickle.load(f)

options = ["orschlägen", " häufig", " beweglich"]
# %%
PROMPT_START, PROMPT_END = 2000, 3000
file_name_append = "_2000"
# %%
def compute_and_conditions(option, type: Literal["logits", "loss"]):
    ANSWER_TOKEN_ID = model.to_tokens(option).flatten()[-1].item()
    prompts = all_prompts[option][PROMPT_START:PROMPT_END]
    multiplier = 1 if type == "loss" else -1

    def get_value(prompts, activated=False):
        if activated:
            if type == "logits":
                _, _, _, value = haystack_utils.get_direct_effect(prompts, model, 
                        context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks,
                        return_type=type, pos=-2)
                return value[:, ANSWER_TOKEN_ID].mean().item()
            elif type == "loss":
                _, _, _, value = haystack_utils.get_direct_effect(prompts, model, 
                        context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks,
                        return_type=type, pos=-1)
                return value.mean().item()
        with model.hooks(fwd_hooks=deactivate_neurons_fwd_hooks):
            if type == "logits":
                return model(prompts, return_type="logits", loss_per_token=True)[:, -2, ANSWER_TOKEN_ID].mean().item()
            elif type == "loss":
                return model(prompts, return_type="loss", loss_per_token=True)[:, -1].mean().item()

    
    # COMPUTE AND CONDITIONS
    ablated_prompts_ny = haystack_utils.create_ablation_prompts(prompts, "NYY", common_tokens)
    ablated_prompts_yn = haystack_utils.create_ablation_prompts(prompts, "YNY", common_tokens)
    ablated_prompts_nn = haystack_utils.create_ablation_prompts(prompts, "NNY", common_tokens)

    yyy_value = get_value(prompts, activated=True)
    nyy_value = get_value(ablated_prompts_ny, activated=True)
    yny_value = get_value(ablated_prompts_yn, activated=True)
    nny_value = get_value(ablated_prompts_nn, activated=True)

    yyn_value = get_value(prompts, activated=False)
    nyn_value = get_value(ablated_prompts_ny, activated=False)
    ynn_value = get_value(ablated_prompts_yn, activated=False)
    nnn_value = get_value(ablated_prompts_nn, activated=False)

    # Fix current token
    yyn_nyn_diff = (nyn_value - yyn_value) * multiplier
    nyy_nyn_diff = (nyn_value - nyy_value) * multiplier
    yyy_nyn_diff =  (nyn_value - yyy_value) * multiplier
    current_diffs = yyy_nyn_diff - (yyn_nyn_diff + nyy_nyn_diff)

    # Fix previous token
    yyn_ynn_diff = (ynn_value - yyn_value) * multiplier
    yny_ynn_diff = (ynn_value - yny_value) * multiplier
    yyy_ynn_diff =  (ynn_value - yyy_value) * multiplier
    previous_diffs = yyy_ynn_diff - (yyn_ynn_diff + yny_ynn_diff)

    # Fix context neuron
    yyy_nny_diff = (nny_value - yyy_value) * multiplier
    nyy_nny_diff = (nny_value - nyy_value) * multiplier 
    yny_nny_diff =  (nny_value - yny_value) * multiplier
    context_diffs = yyy_nny_diff - (nyy_nny_diff + yny_nny_diff)

    # All groups of two features
    nyy_nnn_diff = (nnn_value - nyy_value) * multiplier
    yny_nnn_diff = (nnn_value - yny_value) * multiplier
    yyn_nnn_diff = (nnn_value - yyn_value) * multiplier
    # Individual features
    nny_nnn_diff = (nnn_value - nny_value) * multiplier
    nyn_nnn_diff = (nnn_value - nyn_value) * multiplier
    ynn_nnn_diff = (nnn_value - ynn_value) * multiplier
    # Loss increase for all features
    yyy_nnn_diff = (nnn_value - yyy_value) * multiplier

    individual_diffs = yyy_nnn_diff - (nny_nnn_diff + nyn_nnn_diff + ynn_nnn_diff)
    two_feature_diffs = yyy_nnn_diff - (nyy_nnn_diff + yny_nnn_diff + yyn_nnn_diff)/2

    # Merge current and previous tokens
    merged_tokens_diff = yyy_nnn_diff - (yyn_nnn_diff + nny_nnn_diff)

    result = {
        "NNN": nnn_value,
        "NNY": nny_value,
        "NYN": nyn_value,
        "YNN": ynn_value,
        "NYY": nyy_value,
        "YNY": yny_value,
        "YYN": yyn_value,
        "YYY": yyy_value,
        "Fix Current": current_diffs,
        "Fix Previous": previous_diffs,
        "Fix Context": context_diffs,
        "Single Feature": individual_diffs,
        "Two Features": two_feature_diffs,
        "Merge Tokens": merged_tokens_diff,
    }

    return result


#%%
for type in ["loss", "logits"]:
    all_res = {}
    for option in options:
    
        all_res[option] = compute_and_conditions(option, type)
    df = pd.DataFrame(all_res).round(2)
    df.to_csv(f"data/and_neurons/and_conditions_{type}{file_name_append}.csv")


# %% 
# Get average activation for each neuron on random German prompts
pre_act = haystack_utils.get_mlp_activations(german_data, 5, model, 200, hook_pre=True, mean=False)
post_act = haystack_utils.get_mlp_activations(german_data, 5, model, 200, hook_pre=False, mean=False)

# %%
def normalize_df(df, hook_name):
    df = df.copy()
    activations = pre_act if hook_name == "hook_pre" else post_act
    std = activations.std(axis=0).cpu().numpy()
    columns = ["YYY", "YYN", "YNY", "NYY", "YNN", "NYN", "NNY", "NNN"]
    for col in columns:
        df[col] = df[col] / std
    return df


dfs = {}
for option in tqdm(options):
    dfs[option] = {}
    prompts = all_prompts[option][PROMPT_START:PROMPT_END]
    for hook_name in ["hook_pre", "hook_post"]:
        dfs[option][hook_name] = {}
        for scale in [True, False]:
            df = haystack_utils.activation_data_frame(option, prompts, model, common_tokens, activate_neurons_fwd_hooks, deactivate_neurons_fwd_hooks, mlp_hook=hook_name)
            if scale:
                df = normalize_df(df, hook_name)
            dfs[option][hook_name]["Scaled" if scale else "Unscaled"] = df
# %%
with open(f"data/and_neurons/activation_dfs{file_name_append}.pkl", "wb") as f:
    pickle.dump(dfs, f)
# %%


for option in options:
    for hook_name in ["hook_pre", "hook_post"]:
        for scale in [True, False]:
            df = dfs[option][hook_name]["Scaled" if scale else "Unscaled"]
            df["Two Features (diff)"] = (df["YYY"] - df["NNN"]) - ((df["YNY"] - df["NNN"]) + (df["NYY"] - df["NNN"]) + (df["YYN"] - df["NNN"]))/2
            df["Single Features (diff)"] = (df["YYY"] - df["NNN"]) - ((df["YNN"] - df["NNN"]) + (df["NYN"] - df["NNN"]) + (df["NNY"] - df["NNN"]))
            df["Current Token (diff)"] = ((df["YYY"] - df["NYN"]) - ((df["YYN"] - df["NYN"]) + (df["NYY"] - df["NYN"])))
            df["Previous Token (diff)"] = ((df["YYY"] - df["YNN"]) - ((df["YYN"] - df["YNN"]) + (df["YNY"] - df["YNN"])))
            df["Context Neuron (diff)"] = ((df["YYY"] - df["NNY"]) - ((df["YNY"] - df["NNY"]) + (df["NYY"] - df["NNY"])))
            df["Merge Tokens (diff)"] = ((df["YYY"] - df["NNN"]) - ((df["YYN"] - df["NNN"]) + (df["NNY"] - df["NNN"])))
            df["Two Features (AND)"] = ((df["YYY"] - df["NNN"]) > ((df["YNY"] - df["NNN"]) + (df["NYY"] - df["NNN"]) + (df["YYN"] - df["NNN"]))/2) & (df["YYY"]>0)
            df["Single Features (AND)"] = ((df["YYY"] - df["NNN"]) > ((df["YNN"] - df["NNN"]) + (df["NYN"] - df["NNN"]) + (df["NNY"] - df["NNN"]))) & (df["YYY"]>0)
            df["Current Token (AND)"] = ((df["YYY"] - df["NYN"]) > ((df["YYN"] - df["NYN"]) + (df["NYY"] - df["NYN"]))) & (df["YYY"]>0)
            df["Previous Token (AND)"] = ((df["YYY"] - df["YNN"]) > ((df["YYN"] - df["YNN"]) + (df["YNY"] - df["YNN"]))) & (df["YYY"]>0)
            df["Context Neuron (AND)"] = ((df["YYY"] - df["NNY"]) > ((df["YNY"] - df["NNY"]) + (df["NYY"] - df["NNY"]))) & (df["YYY"]>0)
            df["Merge Tokens (AND)"] = ((df["YYY"] - df["NNN"]) > ((df["YYN"] - df["NNN"]) + (df["NNY"] - df["NNN"]))) & (df["YYY"]>0)
            df["Two Features (NEG AND)"] = ((df["YYY"] - df["NNN"]) < ((df["YNY"] - df["NNN"]) + (df["NYY"] - df["NNN"]) + (df["YYN"] - df["NNN"]))/2) & (df["NNN"]>0)
            df["Single Features (NEG AND)"] = ((df["YYY"] - df["NNN"]) < ((df["YNN"] - df["NNN"]) + (df["NYN"] - df["NNN"]) + (df["NNY"] - df["NNN"]))) & (df["NNN"]>0)
            df["Current Token (NEG AND)"] = ((df["YYY"] - df["NYN"]) < ((df["YYN"] - df["NYN"]) + (df["NYY"] - df["NYN"]))) & (df["NNN"]>0)
            df["Previous Token (NEG AND)"] = ((df["YYY"] - df["YNN"]) < ((df["YYN"] - df["YNN"]) + (df["YNY"] - df["YNN"]))) & (df["NNN"]>0)
            df["Context Neuron (NEG AND)"] =((df["YYY"] - df["NNY"]) < ((df["YNY"] - df["NNY"]) + (df["NYY"] - df["NNY"]))) & (df["NNN"]>0)
            df["Merge Tokens (NEG AND)"] = ((df["YYY"] - df["NNN"]) < ((df["YYN"] - df["NNN"]) + (df["NNY"] - df["NNN"]))) & (df["NNN"]>0)
            df["Greater Than All"] = (df["YYY"] > df["NNN"]) & (df["YYY"] > df["YNN"]) & (df["YYY"] > df["NYN"]) & (df["YYY"] > df["NNY"]) & (df["YYY"] > df["YYN"]) & (df["YYY"] > df["NYY"]) & (df["YYY"] > df["YNY"])
            df["Smaller Than All"] = (df["YYY"] < df["NNN"]) & (df["YYY"] < df["YNN"]) & (df["YYY"] < df["NYN"]) & (df["YYY"] < df["NNY"]) & (df["YYY"] < df["YYN"]) & (df["YYY"] < df["NYY"]) & (df["YYY"] < df["YNY"])
            dfs[option][hook_name]["Scaled" if scale else "Unscaled"] = df

with open(f"data/and_neurons/activation_dfs{file_name_append}.pkl", "wb") as f:
    pickle.dump(dfs, f)
# %%

for option in tqdm(options):
    prompts = all_prompts[option][PROMPT_START:PROMPT_END]

    # Ablation loss should be identical for all settings
    df = dfs[option]["hook_post"]["Unscaled"]

    original_loss, ablated_loss = haystack_utils.compute_mlp_loss(prompts, model, df, torch.LongTensor([i for i in range(model.cfg.d_mlp)]).cuda(), compute_original_loss=True, ablate_mode="YYN")
    print(original_loss, ablated_loss)

    ablated_losses = []
    for neuron in tqdm(range(model.cfg.d_mlp)):
        ablated_loss = haystack_utils.compute_mlp_loss(prompts, model, df, torch.LongTensor([neuron]).cuda(), ablate_mode="YYN")
        ablated_losses.append(ablated_loss)

    ablation_loss_increase = np.array(ablated_losses) - original_loss

    for hook_name in ["hook_pre", "hook_post"]:
        for scale in [True, False]:
            df = dfs[option][hook_name]["Scaled" if scale else "Unscaled"]
            df["AblationLossIncrease"] = ablation_loss_increase
            dfs[option][hook_name]["Scaled" if scale else "Unscaled"] = df

with open(f"data/and_neurons/activation_dfs{file_name_append}.pkl", "wb") as f:
    pickle.dump(dfs, f)
# %%
with open(f"data/and_neurons/activation_dfs{file_name_append}.pkl", "rb") as f:
    dfs = pickle.load(f)

#%%


for PROMPT_START in [0, 1000, 2000]:
    PROMPT_END = PROMPT_START + 1000
    file_name_append = f"_{str(PROMPT_START)[0]}000"

    with open(f"data/and_neurons/activation_dfs{file_name_append}.pkl", "rb") as f:
        dfs = pickle.load(f)
    # Ablation losses
    all_losses = {}
    for option in tqdm(options):
        all_losses[option] = {}
        prompts = all_prompts[option][PROMPT_START:PROMPT_END]
        for hook_name in ["hook_pre", "hook_post"]:
            all_losses[option][hook_name] = {}
            for scale in [True, False]:
                all_losses[option][hook_name]["Scaled" if scale else "Unscaled"] = {}
                df = dfs[option][hook_name]["Scaled" if scale else "Unscaled"]
                unscaled_df = dfs[option][hook_name]["Unscaled"]
                
                for include_mode in ["All Positive", "Greater Positive", "All Negative", "Smaller Negative"]:
                    all_losses[option][hook_name]["Scaled" if scale else "Unscaled"][include_mode] = {}
                    original_loss = haystack_utils.compute_path_patched_mlp_loss(prompts, model, torch.LongTensor([]),
                                                        context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks)
                    with model.hooks(deactivate_neurons_fwd_hooks):
                        all_ablated_loss = model(prompts, return_type="loss", loss_per_token=True)[:, -1].mean().item()
                    all_losses[option][hook_name]["Scaled" if scale else "Unscaled"][include_mode]["All"] = {
                        "Original": original_loss,
                        "All Ablated": all_ablated_loss,
                    }
                    for feature_mode in ["Two Features", "Single Features", "Current Token", "Previous Token", "Context Neuron", "Merge Tokens"]:
                        if include_mode == "Greater Positive":
                            neurons = df[df[feature_mode + " (AND)"] & df["Greater Than All"]].index
                        elif include_mode == "All Positive":
                            neurons = df[df[feature_mode + " (AND)"]].index
                        elif include_mode == "Smaller Negative":
                            neurons = df[df[feature_mode + " (NEG AND)"] & df["Smaller Than All"]].index
                        elif include_mode == "All Negative":
                            neurons = df[df[feature_mode + " (NEG AND)"]].index
                        elif include_mode == f"Positive and Negative (Top {k//2})":
                            neurons_top = haystack_utils.get_top_k_neurons(df, (df["NNN"]>0), feature_mode + " (diff)", k//2, ascending=True)
                            neurons_bottom = haystack_utils.get_top_k_neurons(df, (df["YYY"]>0), feature_mode + " (diff)", k//2)
                            neurons = np.concatenate([neurons_top, neurons_bottom])
                        else:
                            assert False, f"Invalid include mode: {include_mode}"
                        neurons = torch.LongTensor(neurons.tolist())

                        ablated_loss = haystack_utils.compute_path_patched_mlp_loss(prompts, model, neurons,
                                                        context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks)
                        all_losses[option][hook_name]["Scaled" if scale else "Unscaled"][include_mode]["All"][feature_mode+f" (N={neurons.shape[0]})"] = ablated_loss

                    for k in [5, 10, 25, 50]:
                        #original_loss, all_ablated_loss = haystack_utils.compute_mlp_loss(prompts, model, unscaled_df, torch.LongTensor([i for i in range(model.cfg.d_mlp)]), ablate_mode="YYN", compute_original_loss=True)
                        original_loss = haystack_utils.compute_path_patched_mlp_loss(prompts, model, torch.LongTensor([]),
                                                            context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks)
                        with model.hooks(deactivate_neurons_fwd_hooks):
                            all_ablated_loss = model(prompts, return_type="loss", loss_per_token=True)[:, -1].mean().item()
                        all_losses[option][hook_name]["Scaled" if scale else "Unscaled"][include_mode][str(k)] = {
                            "Original": original_loss,
                            "All Ablated": all_ablated_loss,
                        }
                        for feature_mode in ["Two Features", "Single Features", "Current Token", "Previous Token", "Context Neuron", "Merge Tokens"]:
                            if include_mode == f"Greater Positive":
                                neurons = haystack_utils.get_top_k_neurons(df, (df["YYY"]>0)&(df["Greater Than All"]), feature_mode + " (diff)", k)
                            elif include_mode == f"All Positive":
                                neurons = haystack_utils.get_top_k_neurons(df, (df["YYY"]>0), feature_mode + " (diff)", k)
                            elif include_mode == f"Smaller Negative":
                                neurons = haystack_utils.get_top_k_neurons(df, (df["NNN"]>0)&(df["Smaller Than All"]), feature_mode + " (diff)", k, ascending=True)
                            elif include_mode == f"All Negative":
                                neurons = haystack_utils.get_top_k_neurons(df, (df["NNN"]>0), feature_mode + " (diff)", k, ascending=True)
                            elif include_mode == f"Positive and Negative":
                                neurons_top = haystack_utils.get_top_k_neurons(df, (df["NNN"]>0), feature_mode + " (diff)", k//2, ascending=True)
                                neurons_bottom = haystack_utils.get_top_k_neurons(df, (df["YYY"]>0), feature_mode + " (diff)", k//2)
                                neurons = np.concatenate([neurons_top, neurons_bottom])
                            else:
                                assert False, f"Invalid include mode: {include_mode}"
                            neurons = torch.LongTensor(neurons.tolist())

                            #ablated_loss = haystack_utils.compute_mlp_loss(prompts, model, unscaled_df, neurons, ablate_mode="YYN")
                            ablated_loss = haystack_utils.compute_path_patched_mlp_loss(prompts, model, neurons,
                                                            context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks)
                            all_losses[option][hook_name]["Scaled" if scale else "Unscaled"][include_mode][str(k)][feature_mode+f" (N={neurons.shape[0]})"] = ablated_loss

    with open(f"data/and_neurons/ablation_losses{file_name_append}.json", "w") as f:
        json.dump(all_losses, f, indent=4)
# %%

# Dataset prompts

# The random + ngram prompt
# %%
# %%
prompts = all_prompts["orschlägen"][:500]
neurons = torch.LongTensor([i for i in range(2000)])
ablated_loss = haystack_utils.compute_path_patched_mlp_loss(prompts, model, neurons,
                        context_ablation_hooks=deactivate_neurons_fwd_hooks, context_activation_hooks=activate_neurons_fwd_hooks)
ablated_loss
# %%


# %%
import torch

diffs = dfs["orschlägen"]["hook_post"]["Scaled"]["Context Neuron (diff)"].tolist()
diffs.sort()
px.line(diffs)
#%%
# Compute the quartiles
data = torch.Tensor(diffs)
Q1 = torch.quantile(data, 0.10)
Q3 = torch.quantile(data, 0.90)

# Compute the IQR
IQR = Q3 - Q1
# Compute the bounds
lower_bound = Q1 - 1.5 * IQR
upper_bound = Q3 + 1.5 * IQR
print(Q1, Q3, IQR, lower_bound, upper_bound)

# Find the outliers
outliers = data[(data > upper_bound)]
len(outliers)
# %%
