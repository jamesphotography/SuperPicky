# -*- coding: utf-8 -*-
"""
SVDLUT 调色离线预览(本机)/ offline SVDLUT color preview.

用官方实现(P0 已 JIT 编译的 CPU 算子 + vendored 生成器 FiveK 权重)对任意图片出
原图 vs 调色 的交互式 before/after 网页(可拖竖线、切 40/70/100% 强度),并自动打开。
仅本机评测用,不进打包。

用法 / Usage:
    .venv/bin/python scripts/svdlut_reference/demo_color.py /path/to/photo.jpg
"""
from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import sys
import types

import cv2
import numpy as np
import torch
from torch.utils.cpp_extension import load

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
WORK = "/tmp/svdlut_ref"
OUT = "/tmp/enhance_demo/color_preview.html"


def _build_net():
    """编译官方 CPU 参考算子 + 构造 vendored 网络(FiveK 权重)。"""
    ref_op = load(name="svdlut_ref_op",
                  sources=[os.path.join(_HERE, "ref_binding.cpp"),
                           os.path.join(WORK, "trilinear2D_slice_LUTTransform_cpu.cpp")],
                  verbose=False)
    sys.modules["cpp_ext_interface"] = types.ModuleType("cpp_ext_interface")
    sys.modules["cpp_ext_interface"].bilinear_2Dslicing_lut_transform = None
    spec = importlib.util.spec_from_file_location("svd_models", os.path.join(WORK, "models.py"))
    svd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(svd)
    net = svd.SVDLUT(backbone_type="cnn", backbone_coef=8,
                     lut_n_vertices=33, lut_n_ranks=8, lut_n_singular=8,
                     grid_n_vertices=17, grid_n_ranks=8, grid_n_singular=8, ch_per_grid=2,
                     lut_weight_ranks=8, grid_weight_ranks=8)
    from config import (get_install_scoped_resource_path, get_packaged_model_relative_path)
    wp = str(get_install_scoped_resource_path(
        "models/svdlut.pth",
        packaged_relative_path=get_packaged_model_relative_path("models/svdlut.pth")))
    sd = torch.load(wp, map_location="cpu", weights_only=True)
    net.load_state_dict(sd.get("state_dict", sd), strict=True)
    net.eval()
    return ref_op, net


def _grade(ref_op, net, bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).contiguous()
    with torch.no_grad():
        f = net.backbone(x)
        lut, _ = net.gen_2d_lut(f)
        lw, lb = net.gen_2d_lut_weight_bias(f)
        g, _ = net.gen_2d_bilateral(f)
        gw, gb = net.gen_2d_grid_weight_bias(f)
        o = torch.relu(ref_op.forward(g.contiguous(), x.contiguous(), gw.contiguous(),
                                      gb.contiguous(), lut.contiguous(), lw.contiguous(),
                                      lb.contiguous())).clamp(0, 1)
    return cv2.cvtColor((o.squeeze(0).permute(1, 2, 0).numpy() * 255 + .5).astype(np.uint8),
                        cv2.COLOR_RGB2BGR)


def _b64(bgr) -> str:
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return base64.b64encode(buf).decode()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: demo_color.py <图片路径>")
        return 2
    src = sys.argv[1]
    bgr = cv2.imread(src, cv2.IMREAD_COLOR)  # TIF/JPEG/PNG → 8-bit BGR
    if bgr is None:
        print(f"[错误] 无法读取(可能是 RAW,需先转 TIF/JPEG): {src}")
        return 2
    h, w = bgr.shape[:2]
    s = 1000 / max(h, w)
    if s < 1:
        bgr = cv2.resize(bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

    ref_op, net = _build_net()
    full = _grade(ref_op, net, bgr).astype(np.float32)
    base = bgr.astype(np.float32)
    imgs = {"0": bgr}
    for p in (40, 70, 100):
        imgs[str(p)] = (base * (1 - p / 100) + full * (p / 100) + .5).astype(np.uint8)
    for p in (40, 70, 100):
        d = float(np.abs(imgs[str(p)].astype(int) - bgr.astype(int)).mean())
        print(f"  {p}% 平均像素差 = {d:.1f}")

    data = {k: _b64(v) for k, v in imgs.items()}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html = _HTML.format(name=os.path.basename(src), b0=data["0"],
                        b40=data["40"], b70=data["70"], b100=data["100"])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"预览页 → {OUT}")
    try:
        subprocess.run(["open", OUT], check=False)
    except Exception:  # noqa: BLE001
        pass
    return 0


_HTML = """<!doctype html><meta charset=utf-8><title>SVDLUT 调色预览</title>
<style>body{{background:#111;color:#ccc;font-family:sans-serif;text-align:center;margin:0;padding:16px}}
.wrap{{position:relative;max-width:1000px;margin:12px auto;user-select:none}}
.wrap>img{{display:block;width:100%}}.top{{position:absolute;top:0;left:0;overflow:hidden;width:50%}}
.bar{{position:absolute;top:0;bottom:0;width:2px;background:#fff;left:50%}}
.h{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:34px;height:34px;border-radius:50%;background:#fff;cursor:ew-resize}}
button{{background:#222;color:#ccc;border:1px solid #444;padding:8px 16px;margin:4px;border-radius:6px;cursor:pointer;font-size:15px}}
button.on{{background:#0a84ff;color:#fff;border-color:#0a84ff}}.lbl{{position:absolute;top:8px;padding:2px 8px;background:#0008;border-radius:4px;font-size:13px}}</style>
<h2>SVDLUT (FiveK sRGB) 调色 — {name}</h2>
<div>调色强度: <button data-p=40 class=on>40%</button><button data-p=70>70%</button><button data-p=100>100%(满)</button></div>
<div class=wrap id=w>
 <img id=before src="data:image/jpeg;base64,{b0}">
 <span class=lbl style=right:8px>调色后</span>
 <div class=top id=top><img id=after style="width:1000px" src="data:image/jpeg;base64,{b40}"><span class=lbl style=left:8px>原图</span></div>
 <div class=bar id=bar><div class=h></div></div>
</div>
<script>
const A={{40:"{b40}",70:"{b70}",100:"{b100}"}};
const w=document.getElementById('w'),top=document.getElementById('top'),bar=document.getElementById('bar'),after=document.getElementById('after');
function setX(cx){{let r=w.getBoundingClientRect();let p=Math.max(0,Math.min(1,(cx-r.left)/r.width));top.style.width=(p*100)+'%';bar.style.left=(p*100)+'%';after.style.width=r.width+'px';}}
let drag=false;w.addEventListener('mousedown',e=>{{drag=true;setX(e.clientX)}});window.addEventListener('mousemove',e=>{{if(drag)setX(e.clientX)}});window.addEventListener('mouseup',()=>drag=false);
document.querySelectorAll('button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on');after.src="data:image/jpeg;base64,"+A[b.dataset.p];}});
window.addEventListener('resize',()=>setX(w.getBoundingClientRect().left+w.getBoundingClientRect().width/2));
setTimeout(()=>setX(w.getBoundingClientRect().left+w.getBoundingClientRect().width/2),60);
</script>"""


if __name__ == "__main__":
    raise SystemExit(main())
