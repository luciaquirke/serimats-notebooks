import torch
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.colors import qualitative
from haystack_utils import get_mlp_activations, get_neurons_by_layer
from transformer_lens import HookedTransformer
import scipy.stats as stats
from scipy.stats import skew, kurtosis
import pandas as pd

def line(x, xlabel="", ylabel="", title="", xticks=None, width=800, yaxis=None, hover_data=None, show_legend=True, plot=True):
    
    # Avoid empty plot when x contains a single element
    if len(x) > 1:
        fig = px.line(x, title=title)
    else:
        fig = px.scatter(x, title=title)

    fig.update_layout(xaxis_title=xlabel, yaxis_title=ylabel, width=width, showlegend=show_legend)
    if xticks != None:
        fig.update_layout(
            xaxis = dict(
            tickmode = 'array',
            tickvals = [i for i in range(len(xticks))],
            ticktext = xticks,
            range=[-0.2, len(xticks)-0.8] 
            ),
            yaxis=yaxis,
        )
    
    #fig.update_yaxes(range=[3.45, 3.85])
    if hover_data != None:
        fig.update(data=[{'customdata': hover_data, 'hovertemplate': "Loss: %{y:.4f} (+%{customdata:.2f}%)"}])
    if plot:
        fig.show()
    else:
        return fig
    

def plot_barplot(data: list[list[float]], names: list[str], short_names = None, xlabel="", ylabel="", title="", 
                 width=1000, yaxis=None, show=True, legend=True, yrange=None, confidence_interval=False):
    means = np.mean(data, axis=1)
    if confidence_interval:
        errors = [stats.sem(d)*stats.t.ppf((1+0.95)/2., len(d)-1) for d in data]
    else:
        errors = np.std(data, axis=1)

    fig = go.Figure()
    if short_names is None:
        short_names = names
    if len(data[0]) > 1:
        for i in range(len(names)):
            fig.add_trace(go.Bar(
                x=[short_names[i]],
                y=[means[i]],
                error_y=dict(
                    type='data',
                    array=[errors[i]],
                    visible=True
                ),
                name=names[i]
            ))
    else:
        for i in range(len(names)):
            fig.add_trace(go.Bar(
                x=[short_names[i]],
                y=[means[i]],
                name=names[i]
            ))
    
    
    if yrange is not None:
        fig.update_yaxes(range=yrange)
        
    fig.update_layout(
        title=title,
        yaxis=yaxis,
        xaxis_title=xlabel,
        yaxis_title=ylabel,
        barmode='group',
        width=width,
        showlegend=legend
    )

    
    if show:
        fig.show()
    else: 
        return fig


def color_binned_histogram(data, ranges, labels, title):
    # Check that ranges and labels are of the same length
    if len(ranges) != len(labels):
        raise ValueError("Length of ranges and labels should be the same")

    fig = go.Figure()

    # Plot data in ranges with specific colors
    for r, label, color in zip(ranges, labels, qualitative.Plotly):
        hist_data = [i for i in data if r[0] <= i < r[1]]
        fig.add_trace(go.Histogram(x=hist_data, 
                                    name=label,
                                    marker_color=color))
    
    # Plot data outside ranges with a gray color
    out_range_data = [i for i in data if not any(r[0] <= i < r[1] for r in ranges)]
    fig.add_trace(go.Histogram(x=out_range_data, 
                                name='Outside Ranges',
                                marker_color='gray'))

    fig.update_layout(barmode='stack',
                      xaxis_title='Value',
                      yaxis_title='Count',
                      title=title,
                      width=1200)
    fig.show()


def plot_neuron_acts(
        model: HookedTransformer, data: list[str], neurons: list[tuple[int, int]], disable_tqdm=True, 
        width=700, range_x=None, hook_pre=False
) -> None:
    '''Plot activation histograms for each neuron specified'''
    neurons_by_layer = get_neurons_by_layer(neurons)
    for layer, layer_neurons in neurons_by_layer.items():
        acts = get_mlp_activations(data, layer, model, neurons=torch.tensor(layer_neurons), mean=False, 
                                   disable_tqdm=disable_tqdm, hook_pre=hook_pre).cpu()
        for i, neuron in enumerate(layer_neurons):
            fig = px.histogram(acts[:, i], title=f"L{layer}N{neuron}", width=width)
            fig.update_xaxes(range=range_x)
            fig.show()


def get_neuron_moments(
        model: HookedTransformer, data: list[str], neurons: list[tuple[int, int]], disable_tqdm=True,
        hook_pre=False
) -> pd.DataFrame:
    '''Plot activation histograms for each neuron specified'''
    neurons_by_layer = get_neurons_by_layer(neurons)
    neuron_moments = []
    for layer, layer_neurons in neurons_by_layer.items():
        acts = get_mlp_activations(data, layer, model, neurons=torch.tensor(layer_neurons), mean=False, 
                                   disable_tqdm=disable_tqdm, hook_pre=hook_pre).cpu()
        for i, neuron in enumerate(layer_neurons):
            tensor_numpy = acts[:, i].numpy()
            neuron_moments.append((layer, neuron, skew(tensor_numpy), kurtosis(tensor_numpy)))
    return pd.DataFrame(neuron_moments, columns=['layer', 'neuron', 'skew', 'kurtosis'])
