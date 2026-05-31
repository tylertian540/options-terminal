cmake_minimum_required(VERSION 3.25)
project(OptionsCore VERSION 1.0.0 LANGUAGES CXX)

# ─── 编译标准 & 优化 ───
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# 机构级优化选项
add_compile_options(
  $<$<CONFIG:Release>:-O3>
  $<$<CONFIG:Release>:-march=native>
  $<$<CONFIG:Release>:-ffast-math>
  $<$<CONFIG:Debug>:-g>
  $<$<CONFIG:Debug>:-fsanitize=address,undefined>
  -Wall -Wextra -Wpedantic
)

# ─── 依赖 ───
find_package(Eigen3 3.4 REQUIRED)
find_package(Boost 1.80 REQUIRED COMPONENTS math)
find_package(TBB REQUIRED)     # Intel TBB 并行
find_package(pybind11 REQUIRED)
find_package(GTest REQUIRED)   # 单元测试

# ─── 核心静态库 ───
add_library(options_core_lib STATIC
  core/src/options_math.cpp
  core/src/gamma_squeeze.cpp
  core/src/vol_surface.cpp
)

target_include_directories(options_core_lib PUBLIC
  core/include
  ${EIGEN3_INCLUDE_DIR}
)

target_link_libraries(options_core_lib
  Eigen3::Eigen
  Boost::math
  TBB::tbb
)

# ─── Python 绑定（.so 模块）───
pybind11_add_module(options_core
  core/bindings/options_py.cpp
)

target_link_libraries(options_core PRIVATE options_core_lib)

# 安装到 Python site-packages
install(TARGETS options_core
  DESTINATION ${Python3_SITELIB}
)

# ─── 单元测试 ───
option(BUILD_TESTS "Build unit tests" ON)

if(BUILD_TESTS)
  enable_testing()

  add_executable(options_tests
    core/tests/test_bsm.cpp
    core/tests/test_sabr.cpp
    core/tests/test_gamma_squeeze.cpp
    core/tests/test_vol_surface.cpp
  )

  target_link_libraries(options_tests
    options_core_lib
    GTest::GTest
    GTest::Main
  )

  include(GoogleTest)
  gtest_discover_tests(options_tests)
endif()

# ─── 打印配置摘要 ───
message(STATUS "")
message(STATUS "=== Options Core Build Configuration ===")
message(STATUS "  Build Type:  ${CMAKE_BUILD_TYPE}")
message(STATUS "  C++ Standard: ${CMAKE_CXX_STANDARD}")
message(STATUS "  Eigen3: ${EIGEN3_VERSION}")
message(STATUS "  Build Tests: ${BUILD_TESTS}")
message(STATUS "========================================")
