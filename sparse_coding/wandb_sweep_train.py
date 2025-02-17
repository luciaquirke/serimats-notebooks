# Sweep over autoencoder configurations. To change more than one setting per run, define a configuration 
# file for each model in the sweep then set the param to "cfg_file" and values to a list of file paths
import sys
sys.path.append('../')
import json
from transformer_lens import HookedTransformer
import torch
import numpy as np
import random
import wandb

from process_tiny_stories_data import (
    load_tinystories_validation_prompts,
    load_tinystories_tokens,
)
from sparse_coding.train_autoencoder import get_autoencoder, main, act_name_to_d_in
from utils.autoencoder_utils import AutoEncoderConfig
from utils.haystack_utils import get_device


def add_derived_values(cfg: AutoEncoderConfig) -> AutoEncoderConfig:
    # Accept alternative config values from file specified in command line
    if cfg["cfg_file"] is not None:
        with open(cfg["cfg_file"], "r") as f:
            cfg.update(json.load(f))

    # Derive config values
    cfg["model_batch_size"] = cfg["batch_size"] // cfg["seq_len"] * 16
    cfg["buffer_size"] = cfg["batch_size"] * cfg["buffer_mult"]
    cfg["buffer_batches"] = (
        cfg["buffer_size"] // cfg["seq_len"]
    )  # (cfg["model_batch_size"] * cfg["seq_len"])
    cfg["num_eval_batches"] = cfg["num_eval_tokens"] // cfg["batch_size"]
    assert cfg["buffer_batches"] % cfg["model_batch_size"] == 0
    return cfg


def set_seed(cfg: AutoEncoderConfig) -> int:
    seed = cfg["seed"]
    np.random.seed(seed)
    random.seed(seed)
    return seed


def train(cfg=None):
    with wandb.init(config=cfg):
        # If called by wandb.agent this config will be set by Sweep Controller
        cfg = wandb.config
        # Mutates
        add_derived_values(cfg)
   
        torch.set_grad_enabled(True)
        seed = set_seed(cfg)
        device = get_device()
        prompt_data = load_tinystories_tokens(cfg["data_path"])
        eval_prompts = load_tinystories_validation_prompts(cfg["data_path"])[
            : cfg["num_eval_prompts"]
        ]
        
        model = HookedTransformer.from_pretrained(
            cfg["model"],
            center_unembed=True,
            center_writing_weights=True,
            fold_ln=True,
            device=device,
        )
        cfg["d_in"] = act_name_to_d_in(model, cfg['act'])

        encoder = get_autoencoder(cfg, device, seed)

        main(encoder, model, cfg.as_dict(), prompt_data, eval_prompts, device)


if __name__ == "__main__":
    train()