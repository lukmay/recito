"""
CUDA graph helpers shared by model servers (imported inside the model venvs,
needs torch).

A decode step that calls many small kernels spends most of its time in Python
and kernel launch overhead. Recording it once as a CUDA graph and replaying it
removes that overhead. Graphs need fixed tensor shapes, hence StaticKV.
"""
import contextlib

import torch


class StaticKV:
    """Fixed-length KV cache, duck-typed like a Hugging Face cache (only
    update()). Writes at cache_position and always returns the whole buffer;
    the 4D attention mask decides which positions count."""

    def __init__(self, n_layers, kv_heads, max_len, head_dim, dtype, dev, batch=1):
        shape = (batch, kv_heads, max_len, head_dim)
        self.k = [torch.zeros(shape, dtype=dtype, device=dev) for _ in range(n_layers)]
        self.v = [torch.zeros(shape, dtype=dtype, device=dev) for _ in range(n_layers)]

    def update(self, k, v, layer_idx, cache_kwargs=None):
        pos = cache_kwargs["cache_position"]
        self.k[layer_idx].index_copy_(2, pos, k)
        self.v[layer_idx].index_copy_(2, pos, v)
        return self.k[layer_idx], self.v[layer_idx]

    def load(self, pkv):
        """Copy a prefilled DynamicCache into the buffers -> its length."""
        n = 0
        for li in range(len(self.k)):
            k, v = pkv.layers[li].keys, pkv.layers[li].values
            n = k.shape[2]
            self.k[li][:, :, :n].copy_(k)
            self.v[li][:, :, :n].copy_(v)
        return n


def _modes(autocast_dtype):
    stack = contextlib.ExitStack()
    stack.enter_context(torch.inference_mode())
    if autocast_dtype is not None:
        stack.enter_context(torch.autocast("cuda", dtype=autocast_dtype,
                                           enabled=autocast_dtype != torch.float32))
    return stack


def capture(body, autocast_dtype=None, warmup=3):
    """Run body() `warmup` times on a side stream, then record one call as a
    CUDA graph (under inference mode and optional autocast). body() must only
    read and write tensors that stay allocated. -> torch.cuda.CUDAGraph"""
    s = torch.cuda.Stream()
    s.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(s), _modes(autocast_dtype):
        for _ in range(warmup):
            body()
    torch.cuda.current_stream().wait_stream(s)
    graph = torch.cuda.CUDAGraph()
    with _modes(autocast_dtype), torch.cuda.graph(graph):
        body()
    return graph
