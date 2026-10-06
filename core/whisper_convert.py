"""把 Hugging Face 格式的 Whisper 模型(safetensors)轉成 whisper.cpp 用的格式(ggml)。

用途:有些模型(例如聯發科的 Breeze ASR 25)官方只提供 Hugging Face 格式,whisper.cpp 格式只有來源不明的轉檔;
這裡讓程式從官方下載後在本地自己轉,不用相信別人轉好的檔案。只用 numpy,不需要 PyTorch。
做法照 whisper.cpp 的 models/convert-h5-to-ggml.py(同樣的檔頭、詞表、張量名稱與型別)。
"""

import json
import struct
from pathlib import Path

import numpy as np

# Hugging Face 的張量名稱 -> whisper.cpp 的名稱(層裡面的部分)
_LAYER = {
    "self_attn.k_proj": "attn.key", "self_attn.q_proj": "attn.query", "self_attn.v_proj": "attn.value",
    "self_attn.out_proj": "attn.out", "self_attn_layer_norm": "attn_ln",
    "encoder_attn.q_proj": "cross_attn.query", "encoder_attn.k_proj": "cross_attn.key",
    "encoder_attn.v_proj": "cross_attn.value", "encoder_attn.out_proj": "cross_attn.out",
    "encoder_attn_layer_norm": "cross_attn_ln",
    "fc1": "mlp.0", "fc2": "mlp.2", "final_layer_norm": "mlp_ln",
}
_TOP = {
    "encoder.layer_norm.bias": "encoder.ln_post.bias", "encoder.layer_norm.weight": "encoder.ln_post.weight",
    "encoder.embed_positions.weight": "encoder.positional_embedding",
    "decoder.layer_norm.bias": "decoder.ln.bias", "decoder.layer_norm.weight": "decoder.ln.weight",
    "decoder.embed_positions.weight": "decoder.positional_embedding",
    "decoder.embed_tokens.weight": "decoder.token_embedding.weight",
}
# 這幾個一定存成 32 位元(whisper.cpp 要求;其他一維的也是)
_F32 = {"encoder.conv1.bias", "encoder.conv2.bias", "encoder.positional_embedding", "decoder.positional_embedding"}
_DTYPES = {"F16": np.float16, "F32": np.float32, "BF16": np.uint16}


def read_safetensors(path):
    """回傳 {名稱: (型別, 形狀, 開始位置, 結束位置)} 和資料開始的位置;資料用 memmap 讀,不會整個載入記憶體。"""
    with open(path, "rb") as file:
        size = struct.unpack("<Q", file.read(8))[0]
        header = json.loads(file.read(size))
    header.pop("__metadata__", None)
    return {name: (info["dtype"], info["shape"], *info["data_offsets"]) for name, info in header.items()}, 8 + size


def _tensor(blob, base, info):
    dtype, shape, start, end = info
    if dtype not in _DTYPES:
        raise ValueError(f"不支援的資料型別 {dtype}")
    data = np.frombuffer(blob[base + start:base + end], dtype=_DTYPES[dtype]).reshape(shape)
    if dtype == "BF16":
        data = (data.astype(np.uint32) << 16).view(np.float32)
    return data


def ggml_name(name):
    """model.encoder.layers.3.self_attn.k_proj.weight -> encoder.blocks.3.attn.key.weight;不用的回傳 None。"""
    if name == "proj_out.weight":
        return None                     # 和詞嵌入共用,whisper.cpp 不讀
    parts = name.split(".")[1:] if name.startswith("model.") else name.split(".")
    if len(parts) > 2 and parts[1] == "layers":
        layer = ".".join(parts[3:-1])
        return ".".join([parts[0], "blocks", parts[2], _LAYER[layer], parts[-1]])
    joined = ".".join(parts)
    return _TOP.get(joined, joined)


def mel_filters(n_mels, rate=16000, n_fft=400):
    """梅爾濾波器(和 librosa.filters.mel 預設的 Slaney 公式一樣,也就是 Whisper 的 mel_filters.npz)。"""
    def hz_to_mel(f):
        f = np.asanyarray(f, dtype=np.float64)
        mels = f / (200.0 / 3)
        log_part = f >= 1000.0
        return np.where(log_part, 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / (np.log(6.4) / 27.0), mels)

    def mel_to_hz(m):
        m = np.asanyarray(m, dtype=np.float64)
        freqs = m * (200.0 / 3)
        return np.where(m >= 15.0, 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0)), freqs)

    fft_freqs = np.linspace(0, rate / 2, 1 + n_fft // 2)
    points = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(rate / 2), n_mels + 2))
    gaps = np.diff(points)
    ramps = points[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / gaps[:-1, None]
    upper = ramps[2:] / gaps[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (points[2:n_mels + 2] - points[:n_mels]))[:, None]
    return weights.astype(np.float32)


def _byte_decoder():
    """GPT-2 詞表把位元組換成可見字元的對照表,反過來用。"""
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {chr(c): b for b, c in zip(bs, cs)}


def convert(folder, output, progress=None, cancel=None):
    """folder 裡要有 config.json、vocab.json、model.safetensors;輸出 16 位元的 ggml 檔(之後可以再壓縮)。
    progress(已處理的位元組, 全部);cancel() 為真時停下並刪掉沒寫完的檔案,回傳 False。"""
    folder, output = Path(folder), Path(output)
    hparams = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    vocab = json.loads((folder / "vocab.json").read_text(encoding="utf-8"))
    # 特殊符號(<|endoftext|> 等)whisper.cpp 會自己加,詞表只放一般的字(和官方轉好的檔案一樣)
    vocab = {token: index for token, index in vocab.items() if not (token.startswith("<|") and token.endswith("|>"))}
    weights = folder / "model.safetensors"
    tensors, base = read_safetensors(weights)
    max_length = hparams.get("max_length") or hparams.get("max_target_positions", 448)
    n_mels = hparams["num_mel_bins"]
    filters = mel_filters(n_mels)
    decoder = _byte_decoder()
    total = sum(end - start for _, _, start, end in tensors.values())
    done = 0
    partial = output.with_name(output.name + ".part")
    blob = np.memmap(weights, dtype=np.uint8, mode="r")
    try:
        with open(partial, "wb") as out:
            out.write(struct.pack("i", 0x67676D6C))
            for value in (hparams["vocab_size"], hparams["max_source_positions"], hparams["d_model"],
                          hparams["encoder_attention_heads"], hparams["encoder_layers"], int(max_length),
                          hparams["d_model"], hparams["decoder_attention_heads"], hparams["decoder_layers"],
                          n_mels, 1):
                out.write(struct.pack("i", value))
            out.write(struct.pack("ii", *filters.shape))
            out.write(filters.astype("<f4").tobytes())
            out.write(struct.pack("i", len(vocab)))
            for token, _ in sorted(vocab.items(), key=lambda item: item[1]):
                text = bytes(decoder[ch] for ch in token)
                out.write(struct.pack("i", len(text)))
                out.write(text)
            for name, info in tensors.items():
                if cancel is not None and cancel():
                    raise InterruptedError
                done += info[3] - info[2]
                mapped = ggml_name(name)
                if mapped is None:
                    continue
                data = np.squeeze(_tensor(blob, base, info))
                if mapped in ("encoder.conv1.bias", "encoder.conv2.bias"):
                    data = data.reshape(data.shape[0], 1)
                f32 = data.ndim < 2 or mapped in _F32
                data = data.astype("<f4" if f32 else "<f2")
                encoded = mapped.encode("utf-8")
                out.write(struct.pack("iii", data.ndim, len(encoded), 0 if f32 else 1))
                for dim in reversed(data.shape):
                    out.write(struct.pack("i", dim))
                out.write(encoded)
                out.write(np.ascontiguousarray(data).tobytes())
                if progress:
                    progress(done, total)
        partial.replace(output)
        return True
    except InterruptedError:
        partial.unlink(missing_ok=True)
        return False
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        del blob
