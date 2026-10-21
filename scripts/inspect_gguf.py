import struct
from pathlib import Path

gguf_path = Path(r"F:\RACoN\Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_Q4_k_m.gguf")

def read_str(f):
    length = struct.unpack("<Q", f.read(8))[0]
    return f.read(length).decode("utf-8", errors="replace")

def read_val(f, val_type):
    # GGUF value types:
    # 0: UINT8, 1: INT8, 2: UINT16, 3: INT16, 4: UINT32, 5: INT32, 6: FLOAT32,
    # 7: BOOL, 8: STRING, 9: ARRAY, 10: UINT64, 11: INT64, 12: FLOAT64
    if val_type == 0: return struct.unpack("<B", f.read(1))[0]
    elif val_type == 1: return struct.unpack("<b", f.read(1))[0]
    elif val_type == 2: return struct.unpack("<H", f.read(2))[0]
    elif val_type == 3: return struct.unpack("<h", f.read(2))[0]
    elif val_type == 4: return struct.unpack("<I", f.read(4))[0]
    elif val_type == 5: return struct.unpack("<i", f.read(4))[0]
    elif val_type == 6: return struct.unpack("<f", f.read(4))[0]
    elif val_type == 7: return bool(struct.unpack("<B", f.read(1))[0])
    elif val_type == 8: return read_str(f)
    elif val_type == 9:
        sub_type = struct.unpack("<I", f.read(4))[0]
        count = struct.unpack("<Q", f.read(8))[0]
        items = []
        for _ in range(min(count, 5)):  # read first few if array
            items.append(read_val(f, sub_type))
        # skip remaining
        # For simplicity, if count > 5, skip parsing full array if primitive
        return f"Array[{sub_type}] len={count}"
    elif val_type == 10: return struct.unpack("<Q", f.read(8))[0]
    elif val_type == 11: return struct.unpack("<q", f.read(8))[0]
    elif val_type == 12: return struct.unpack("<d", f.read(8))[0]
    else: raise ValueError(f"Unknown type {val_type}")

with open(gguf_path, "rb") as f:
    magic = f.read(4)
    version = struct.unpack("<I", f.read(4))[0]
    tensor_count = struct.unpack("<Q", f.read(8))[0]
    metadata_kv_count = struct.unpack("<Q", f.read(8))[0]
    print(f"Magic: {magic}, Version: {version}, Tensors: {tensor_count}, Metadata KV: {metadata_kv_count}")

    metadata = {}
    for i in range(metadata_kv_count):
        key = read_str(f)
        vtype = struct.unpack("<I", f.read(4))[0]
        # skip large arrays like tokens
        if vtype == 9:
            sub_type = struct.unpack("<I", f.read(4))[0]
            arr_len = struct.unpack("<Q", f.read(8))[0]
            val = f"<array of {arr_len} items>"
            # skip array bytes
            if sub_type in (0, 1, 7): f.seek(arr_len, 1)
            elif sub_type in (2, 3): f.seek(arr_len * 2, 1)
            elif sub_type in (4, 5, 6): f.seek(arr_len * 4, 1)
            elif sub_type in (10, 11, 12): f.seek(arr_len * 8, 1)
            elif sub_type == 8:
                for _ in range(arr_len):
                    slen = struct.unpack("<Q", f.read(8))[0]
                    f.seek(slen, 1)
            metadata[key] = val
        else:
            val = read_val(f, vtype)
            metadata[key] = val
            if any(k in key for k in ["arch", "block_count", "context_length", "embedding_length", "layer"]):
                print(f"  {key}: {val}")

    print("\nInteresting Metadata:")
    for k, v in metadata.items():
        if not k.startswith("tokenizer"):
            print(f"  {k}: {v}")
