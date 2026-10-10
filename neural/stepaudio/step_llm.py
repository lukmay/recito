"""
Lean inference for the Step1 LLM of Step-Audio-EditX (3.5B, GQA 48/4, ALiBi
variant with -sqrt(distance), no RoPE).

Replaces vLLM (not available natively on Windows) and the HF remote model,
whose SDPA fallback builds a full LxL bias matrix per step and layer. Here:
static KV cache, one decode step as a CUDA graph (no torch.compile, no Triton),
prefix KV reusable per voice.
"""
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F


def alibi_slopes(n_heads):
    n = 2 ** math.floor(math.log2(n_heads))
    m0 = 2.0 ** (-8.0 / n)
    slopes = torch.pow(m0, torch.arange(1, n + 1))
    if n < n_heads:
        m1 = 2.0 ** (-4.0 / n)
        mm = torch.pow(m1, torch.arange(1, 1 + 2 * (n_heads - n), 2))
        slopes = torch.cat([slopes, mm])
    return slopes.float()


def mm(x, w):
    """x @ w.T for bf16 weights or (int8, scales) per output channel."""
    if isinstance(w, tuple):
        q, sc = w
        if x.numel() // x.shape[-1] > 4:     # prefill: dequantize, the int8 kernel is slow here
            return x @ (q.to(x.dtype) * sc[:, None]).t()
        y = torch._weight_int8pack_mm(x.reshape(-1, x.shape[-1]), q, sc)
        return y.view(*x.shape[:-1], q.shape[0])
    return x @ w.t()


def rmsnorm(x, w, eps):
    var = x.float().pow(2).mean(-1, keepdim=True)
    return (x * torch.rsqrt(var + eps).to(x.dtype)) * w


class StepLM:
    def __init__(self, model_dir, device="cuda", dtype=torch.bfloat16, max_len=4096,
                 out_ids=None, quant=None):
        """out_ids: allowed output tokens (e.g. audio tokens + EOT). Only these
        rows of lm_head and the embedding go to the GPU (saves ~0.9 GB); the
        full embedding table stays in RAM for the prefill. Logits and step()
        inputs are then indices into out_ids."""
        from safetensors import safe_open
        cfg = json.loads((Path(model_dir) / "config.json").read_text())
        self.cfg = cfg
        self.H = cfg["hidden_size"]
        self.nh = cfg["num_attention_heads"]
        self.ng = cfg["num_attention_groups"]
        self.hd = self.H // self.nh
        self.nl = cfg["num_hidden_layers"]
        self.eps = cfg["rms_norm_eps"]
        self.V = cfg["vocab_size"]
        self.dev = torch.device(device)
        self.dtype = dtype
        self.max_len = max_len

        idx = json.loads((Path(model_dir) / "model.safetensors.index.json").read_text())
        wmap = idx["weight_map"]
        handles = {fn: safe_open(str(Path(model_dir) / fn), framework="pt", device="cpu")
                   for fn in set(wmap.values())}
        self.quant = quant

        def raw(k):                     # read tensors on demand (saves RAM)
            return handles[wmap[k]].get_tensor(k)

        def g(k):
            return raw(k).to(self.dev, dtype)

        def lin(*keys):
            w = torch.cat([raw(k) for k in keys], 0).to(self.dev, dtype)
            if quant != "int8":
                return w
            sc = (w.float().abs().amax(1) / 127.0).clamp(min=1e-8)
            q = torch.round(w.float() / sc[:, None]).clamp(-127, 127).to(torch.int8)
            return (q, sc.to(dtype))

        emb = raw("model.embed_tokens.weight").to(dtype)
        head = raw("lm_head.weight").to(dtype)
        if out_ids is not None:
            oi = torch.tensor(out_ids, dtype=torch.long)
            self.embed_cpu = emb.pin_memory()
            self.embed = emb[oi].to(self.dev)           # decode inputs
            self.lm_head = head[oi].to(self.dev)
        else:
            self.embed_cpu = None
            self.embed = emb.to(self.dev)
            self.lm_head = head.to(self.dev)
        del emb, head
        self.norm = g("model.norm.weight")
        self.layers = []
        for i in range(self.nl):
            p = f"model.layers.{i}."
            self.layers.append(dict(
                ln1=g(p + "input_layernorm.weight"),
                qkv=lin(p + "self_attn.q_proj.weight", p + "self_attn.k_proj.weight",
                        p + "self_attn.v_proj.weight"),
                o=lin(p + "self_attn.o_proj.weight"),
                ln2=g(p + "post_attention_layernorm.weight"),
                gu=lin(p + "mlp.gate_proj.weight", p + "mlp.up_proj.weight"),
                down=lin(p + "mlp.down_proj.weight")))
        handles.clear()
        torch.cuda.empty_cache()
        d0 = self.layers[0]["down"]
        self.I = (d0[0] if isinstance(d0, tuple) else d0).shape[1]
        self.slopes = alibi_slopes(self.nh).to(self.dev)          # (nh,)
        kv_shape = (self.nl, 1, self.ng, max_len, self.hd)
        self.kc = torch.zeros(kv_shape, device=self.dev, dtype=dtype)
        self.vc = torch.zeros(kv_shape, device=self.dev, dtype=dtype)
        self.ar = torch.arange(max_len, device=self.dev, dtype=torch.float32)
        # CUDA graph buffers
        self.g_tok = torch.zeros(1, dtype=torch.long, device=self.dev)
        self.g_pos = torch.zeros(1, dtype=torch.long, device=self.dev)
        self.g_logits = None
        self.graph = None

    # ------------------------------------------------------------ core
    def _layers(self, x, pos0, T, bias):
        """x: (1,T,H) tokens at positions pos0..pos0+T-1. bias: (1,nh,T,Lk)."""
        nh, ng, hd = self.nh, self.ng, self.hd
        rep = nh // ng
        for li, L in enumerate(self.layers):
            h = rmsnorm(x, L["ln1"], self.eps)
            qkv = mm(h, L["qkv"])
            q, k, v = qkv.split([nh * hd, ng * hd, ng * hd], -1)
            k = k.view(1, T, ng, hd).transpose(1, 2)
            v = v.view(1, T, ng, hd).transpose(1, 2)
            if isinstance(pos0, int):
                self.kc[li, :, :, pos0:pos0 + T] = k
                self.vc[li, :, :, pos0:pos0 + T] = v
                Lk = pos0 + T
                K = self.kc[li, :, :, :Lk]
                Vv = self.vc[li, :, :, :Lk]
            else:  # tensor position (graph, T == 1)
                self.kc[li].index_copy_(2, pos0, k)
                self.vc[li].index_copy_(2, pos0, v)
                K = self.kc[li]
                Vv = self.vc[li]
            q = q.view(1, T, ng, rep, hd).permute(0, 2, 3, 1, 4).reshape(1, ng, rep * T, hd)
            s = (q @ K.transpose(-1, -2)).float() * (hd ** -0.5)       # (1,ng,rep*T,Lk)
            s = s.view(1, ng, rep, T, -1) + bias.view(1, ng, rep, T, -1)
            p = torch.softmax(s, -1).to(self.dtype).view(1, ng, rep * T, -1)
            o = (p @ Vv).view(1, ng, rep, T, hd).permute(0, 3, 1, 2, 4).reshape(1, T, nh * hd)
            x = x + mm(o, L["o"])
            h = rmsnorm(x, L["ln2"], self.eps)
            gu = mm(h, L["gu"])
            gt, up = gu.split([self.I, self.I], -1)
            x = x + mm(F.silu(gt) * up, L["down"])
        return x

    def _bias_prefill(self, pos0, T):
        Lk = pos0 + T
        i = torch.arange(pos0, Lk, device=self.dev, dtype=torch.float32).view(T, 1)
        j = self.ar[:Lk].view(1, Lk)
        d = i - j
        b = -torch.sqrt(d.clamp(min=0)).unsqueeze(0) * self.slopes.view(-1, 1, 1)
        b = b.masked_fill((d < 0).unsqueeze(0), float("-inf"))
        return b.unsqueeze(0)                                          # (1,nh,T,Lk)

    def _bias_step(self, pos):
        d = pos.float() - self.ar                                      # (L,)
        b = -torch.sqrt(d.clamp(min=0)).view(1, -1) * self.slopes.view(-1, 1)
        b = b.masked_fill((d < 0).view(1, -1), float("-inf"))
        return b.view(1, self.nh, 1, -1)

    @torch.inference_mode()
    def prefill(self, ids, pos0=0, chunk=512):
        """ids: list[int] starting at position pos0. Returns the logits of the last token."""
        t = torch.tensor(ids, dtype=torch.long, device=self.dev)
        out = None
        for s in range(0, len(ids), chunk):
            part = t[s:s + chunk]
            T = part.numel()
            if self.embed_cpu is not None:
                x = self.embed_cpu[part.cpu()].to(self.dev, non_blocking=True).unsqueeze(0)
            else:
                x = self.embed[part].unsqueeze(0)
            x = self._layers(x, pos0 + s, T, self._bias_prefill(pos0 + s, T))
            out = x[:, -1]
        x = rmsnorm(out, self.norm, self.eps)
        return (x @ self.lm_head.t()).float()                          # (1,V)

    def _step_eager(self):
        x = self.embed[self.g_tok].unsqueeze(0)                        # (1,1,H)
        x = self._layers(x, self.g_pos, 1, self._bias_step(self.g_pos))
        x = rmsnorm(x[:, -1], self.norm, self.eps)
        return (x @ self.lm_head.t()).float()

    @torch.inference_mode()
    def capture(self):
        """Capture the decode step as a CUDA graph (once, after loading)."""
        self.g_tok.fill_(1)
        self.g_pos.fill_(0)
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            for _ in range(3):
                self._step_eager()
        torch.cuda.current_stream().wait_stream(s)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph):
            self.g_logits = self._step_eager()
        torch.cuda.synchronize()

    @torch.inference_mode()
    def step(self, tok, pos):
        self.g_tok.fill_(tok)
        self.g_pos.fill_(pos)
        if self.graph is not None:
            self.graph.replay()
            return self.g_logits
        return self._step_eager()

    # ------------------------------------------------------------ prefix cache
    def save_prefix(self, n):
        return (self.kc[:, :, :, :n].clone(), self.vc[:, :, :, :n].clone())

    def load_prefix(self, kv):
        k, v = kv
        n = k.shape[3]
        self.kc[:, :, :, :n].copy_(k)
        self.vc[:, :, :, :n].copy_(v)
        return n
