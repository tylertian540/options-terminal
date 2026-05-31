/**
 * options_py.cpp
 * pybind11 绑定 — 将C++核心引擎暴露给Python
 *
 * 编译命令:
 *   cmake -B build -DPYTHON_EXECUTABLE=$(which python3)
 *   cmake --build build --target options_core
 *
 * Python用法:
 *   import options_core as oc
 *   params = oc.OptionParams(S=450, K=460, T=0.1, r=0.05, q=0, sigma=0.25, type=oc.CALL)
 *   g = oc.BlackScholesMerton.compute(params)
 *   print(f"Delta={g.delta:.4f} Gamma={g.gamma:.6f}")
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/eigen.h>
#include "options_math.hpp"
#include "gamma_squeeze.hpp"

namespace py = pybind11;
using namespace options;

PYBIND11_MODULE(options_core, m) {
    m.doc() = "Goldman Sachs-Level Options Core Engine (C++17)";

    // ─── 枚举 ───
    py::enum_<OptionType>(m, "OptionType")
        .value("CALL", OptionType::CALL)
        .value("PUT",  OptionType::PUT);

    py::enum_<ExerciseStyle>(m, "ExerciseStyle")
        .value("EUROPEAN", ExerciseStyle::EUROPEAN)
        .value("AMERICAN", ExerciseStyle::AMERICAN);

    // ─── OptionParams ───
    py::class_<OptionParams>(m, "OptionParams")
        .def(py::init<>())
        .def(py::init<double, double, double, double, double, double, OptionType>(),
             py::arg("S"), py::arg("K"), py::arg("T"),
             py::arg("r"), py::arg("q"), py::arg("sigma"),
             py::arg("type"))
        .def_readwrite("S",     &OptionParams::S)
        .def_readwrite("K",     &OptionParams::K)
        .def_readwrite("T",     &OptionParams::T)
        .def_readwrite("r",     &OptionParams::r)
        .def_readwrite("q",     &OptionParams::q)
        .def_readwrite("sigma", &OptionParams::sigma)
        .def_readwrite("type",  &OptionParams::type)
        .def("__repr__", [](const OptionParams& p) {
            return "<OptionParams S=" + std::to_string(p.S) +
                   " K=" + std::to_string(p.K) +
                   " T=" + std::to_string(p.T) +
                   " σ=" + std::to_string(p.sigma) + ">";
        });

    // ─── GreeksResult（18个指标全暴露）───
    py::class_<GreeksResult>(m, "GreeksResult")
        .def_readonly("price",           &GreeksResult::price)
        .def_readonly("delta",           &GreeksResult::delta)
        .def_readonly("vega",            &GreeksResult::vega)
        .def_readonly("theta",           &GreeksResult::theta)
        .def_readonly("rho",             &GreeksResult::rho)
        .def_readonly("epsilon",         &GreeksResult::epsilon)
        .def_readonly("gamma",           &GreeksResult::gamma)
        .def_readonly("vanna",           &GreeksResult::vanna)
        .def_readonly("charm",           &GreeksResult::charm)
        .def_readonly("vomma",           &GreeksResult::vomma)
        .def_readonly("veta",            &GreeksResult::veta)
        .def_readonly("vera",            &GreeksResult::vera)
        .def_readonly("dualDelta",       &GreeksResult::dualDelta)
        .def_readonly("dualGamma",       &GreeksResult::dualGamma)
        .def_readonly("speed",           &GreeksResult::speed)
        .def_readonly("zomma",           &GreeksResult::zomma)
        .def_readonly("color",           &GreeksResult::color)
        .def_readonly("ultima",          &GreeksResult::ultima)
        .def_readonly("impliedVol",      &GreeksResult::impliedVol)
        .def_readonly("gammaExposure",   &GreeksResult::gammaExposure)
        .def_readonly("deltaExposure",   &GreeksResult::deltaExposure)
        .def("to_dict", [](const GreeksResult& g) {
            return py::dict(
                "price"_a=g.price, "delta"_a=g.delta, "gamma"_a=g.gamma,
                "vega"_a=g.vega,   "theta"_a=g.theta, "rho"_a=g.rho,
                "vanna"_a=g.vanna, "charm"_a=g.charm, "vomma"_a=g.vomma,
                "speed"_a=g.speed, "zomma"_a=g.zomma, "color"_a=g.color,
                "ultima"_a=g.ultima, "gex"_a=g.gammaExposure,
                "dex"_a=g.deltaExposure
            );
        });

    // ─── BlackScholesMerton ───
    py::class_<BlackScholesMerton>(m, "BlackScholesMerton")
        .def_static("compute",
                    &BlackScholesMerton::compute,
                    py::arg("params"), py::arg("oi") = 0LL,
                    "计算完整18个Greeks。执行时间<1μs。")
        .def_static("implied_vol",
                    &BlackScholesMerton::impliedVol,
                    py::arg("market_price"), py::arg("params"),
                    py::arg("init_vol") = 0.3,
                    "牛顿-拉夫森求隐含波动率");

    // ─── SABRModel ───
    py::class_<SABRModel::Params>(m, "SABRParams")
        .def(py::init<>())
        .def_readwrite("alpha", &SABRModel::Params::alpha)
        .def_readwrite("beta",  &SABRModel::Params::beta)
        .def_readwrite("rho",   &SABRModel::Params::rho)
        .def_readwrite("nu",    &SABRModel::Params::nu);

    py::class_<SABRModel>(m, "SABRModel")
        .def_static("implied_vol", &SABRModel::impliedVol,
                    py::arg("F"), py::arg("K"), py::arg("T"), py::arg("params"),
                    "SABR Hagan近似解隐含波动率")
        .def_static("calibrate", &SABRModel::calibrate,
                    py::arg("F"), py::arg("strikes"), py::arg("market_vols"),
                    py::arg("T"), py::arg("beta") = 0.5,
                    "最小二乘法校准SABR参数");

    // ─── GammaSqueezeEngine ───
    py::class_<GammaSqueezeEngine::OptionChainEntry>(m, "ChainEntry")
        .def(py::init<>())
        .def_readwrite("strike",    &GammaSqueezeEngine::OptionChainEntry::strike)
        .def_readwrite("callGamma", &GammaSqueezeEngine::OptionChainEntry::callGamma)
        .def_readwrite("putGamma",  &GammaSqueezeEngine::OptionChainEntry::putGamma)
        .def_readwrite("callOI",    &GammaSqueezeEngine::OptionChainEntry::callOI)
        .def_readwrite("putOI",     &GammaSqueezeEngine::OptionChainEntry::putOI)
        .def_readwrite("expiry",    &GammaSqueezeEngine::OptionChainEntry::expiry);

    py::class_<GammaSqueezeEngine::GEXProfile>(m, "GEXProfile")
        .def_readonly("spot",                &GammaSqueezeEngine::GEXProfile::spot)
        .def_readonly("strikes",             &GammaSqueezeEngine::GEXProfile::strikes)
        .def_readonly("netGEX",              &GammaSqueezeEngine::GEXProfile::netGEX)
        .def_readonly("callGEX",             &GammaSqueezeEngine::GEXProfile::callGEX)
        .def_readonly("putGEX",              &GammaSqueezeEngine::GEXProfile::putGEX)
        .def_readonly("totalGEX",            &GammaSqueezeEngine::GEXProfile::totalGEX)
        .def_readonly("flipStrike",          &GammaSqueezeEngine::GEXProfile::flipStrike)
        .def_readonly("maxCallWall",         &GammaSqueezeEngine::GEXProfile::maxCallWall)
        .def_readonly("maxPutWall",          &GammaSqueezeEngine::GEXProfile::maxPutWall)
        .def_readonly("squeezeProbability",  &GammaSqueezeEngine::GEXProfile::squeezeProbability);

    py::class_<GammaSqueezeEngine>(m, "GammaSqueezeEngine")
        .def_static("compute_profile",
                    &GammaSqueezeEngine::computeProfile,
                    py::arg("chain"), py::arg("spot"),
                    py::arg("contract_size") = 100.0,
                    "计算完整GEX分布图谱");

    // ─── VolatilitySurface ───
    py::class_<VolatilitySurface::SurfacePoint>(m, "SurfacePoint")
        .def(py::init<>())
        .def_readwrite("strike", &VolatilitySurface::SurfacePoint::strike)
        .def_readwrite("expiry", &VolatilitySurface::SurfacePoint::expiry)
        .def_readwrite("iv",     &VolatilitySurface::SurfacePoint::iv);

    py::class_<VolatilitySurface>(m, "VolatilitySurface")
        .def_static("build_svi_surface",
                    &VolatilitySurface::buildSVISurface,
                    py::arg("points"), py::arg("nK") = 50, py::arg("nT") = 20,
                    "构建SVI波动率曲面，返回Eigen矩阵");
}
