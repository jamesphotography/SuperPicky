// 仅暴露官方 SVDLUT 的 CPU forward launcher,作纯 torch 重写的独立数值参考。
// Expose only the official CPU forward launcher as an independent reference.
// 来源 / Source: WontaeaeKim/SVDLUT kernel_code/bilateral_slicing_LUTTransform (Apache-2.0)
#include <torch/extension.h>

void TriLinearCPU2DSliceAndLUTTransformForwardLaucher(
    const torch::Tensor &grid, const torch::Tensor &input,
    const torch::Tensor &grid_weights, const torch::Tensor &grid_bias,
    const torch::Tensor &lut, const torch::Tensor &lut_weights,
    const torch::Tensor &lut_bias, torch::Tensor output);

torch::Tensor forward(const torch::Tensor &grid, const torch::Tensor &input,
                      const torch::Tensor &grid_weights, const torch::Tensor &grid_bias,
                      const torch::Tensor &lut, const torch::Tensor &lut_weights,
                      const torch::Tensor &lut_bias) {
    auto out = torch::zeros_like(input);
    TriLinearCPU2DSliceAndLUTTransformForwardLaucher(
        grid, input, grid_weights, grid_bias, lut, lut_weights, lut_bias, out);
    return out;
}

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("forward", &forward, "SVDLUT CPU slice+LUT forward (reference)");
}
